"""Lokale FastAPI Web-Oberfläche (100 % offline auf 127.0.0.1) mit 1-Klick-Prüfung & Maus-Schwärzung."""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .csv_io import LoadedCSV, dump_csv_bytes, dump_xlsx_bytes, load_tabular_bytes
from .engine import (
    RedactionEngine,
    detect_pii_warning_columns,
    recommend_default_columns,
    run_residual_pii_check,
)
from .models import RedactionConfig, SpanMatch
from .pdf_export import dump_pdf_bytes
from .pdf_redactor import (
    PdfDocumentData,
    apply_pdf_redactions_to_bytes,
    extract_pdf_document,
    is_pdf_bytes,
    is_pdf_support_available,
    render_pdf_page_png,
)
from .report import build_audit_csv_bytes, build_html_report
from .session import PAGINATION_THRESHOLD, ScanSession, serialize_session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB Schutzlimit
SESSION_TTL_SECONDS = 3600            # Sitzungen nach 60 Min. automatisch aus RAM entfernen


class ToggleMatchRequest(BaseModel):
    match_id: str
    redacted: bool


class ManualRedactRequest(BaseModel):
    row_index: int
    column: str
    start: int
    end: int
    text: str | None = None


class BulkWordActionRequest(BaseModel):
    word: str = ""
    words: list[str] | None = None
    action: str  # "release" | "redact"


class ReplacementUpdateRequest(BaseModel):
    replacement: str = "██████"
    use_typed_placeholders: bool = False


def _split_terms(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.replace("\n", ",").split(",") if x.strip()]
    return []


def _load_uploaded_source(
    raw: bytes,
    filename: str = "",
    delimiter: str | None = None,
) -> tuple[LoadedCSV, PdfDocumentData | None]:
    """Liest CSV, Excel (.xlsx) oder PDF (.pdf) ein."""
    if is_pdf_bytes(raw, filename):
        pdf_doc = extract_pdf_document(raw)
        return pdf_doc.loaded, pdf_doc
    return load_tabular_bytes(raw, filename=filename, delimiter=delimiter), None


def _build_config_from_opts(opts: dict[str, Any]) -> RedactionConfig:
    extra_wl = _split_terms(opts.get("extra_whitelist", []))
    for item in _split_terms(opts.get("applicant_extra_data", [])):
        if item not in extra_wl:
            extra_wl.append(item)

    return RedactionConfig(
        applicant_first_name=str(opts.get("applicant_first_name", "")).strip(),
        applicant_last_name=str(opts.get("applicant_last_name", "")).strip(),
        extra_whitelist=extra_wl,
        extra_blacklist=_split_terms(opts.get("extra_blacklist", [])),
        replacement=str(opts.get("replacement", "██████")) or "██████",
        use_typed_placeholders=bool(opts.get("use_typed_placeholders", False)),
        redact_names=bool(opts.get("redact_names", True)),
        redact_addresses=bool(opts.get("redact_addresses", True)),
        redact_house_numbers=bool(opts.get("redact_house_numbers", True)),
        redact_postal_codes=bool(opts.get("redact_postal_codes", True)),
        redact_locations=bool(opts.get("redact_locations", True)),
        redact_bank=bool(opts.get("redact_bank", True)),
        redact_job_titles=bool(opts.get("redact_job_titles", True)),
        redact_amounts=bool(opts.get("redact_amounts", True)),
        redact_phones=bool(opts.get("redact_phones", True)),
        redact_emails=bool(opts.get("redact_emails", True)),
        redact_websites=bool(opts.get("redact_websites", True)),
        redact_ip_addresses=bool(opts.get("redact_ip_addresses", True)),
        redact_birthdates=bool(opts.get("redact_birthdates", True)),
        redact_license_plates=bool(opts.get("redact_license_plates", True)),
        redact_tax_ids=bool(opts.get("redact_tax_ids", True)),
        redact_companies=bool(opts.get("redact_companies", False)),
        redact_uncertain_by_default=bool(opts.get("redact_uncertain_by_default", True)),
        use_ner=bool(opts.get("use_ner", True)),
        columns=opts.get("columns") or None,
    )


def create_app(engine: RedactionEngine | None = None) -> FastAPI:
    app = FastAPI(title="BLACKTHIS – DSGVO Redactor (Lokal & Offline)", docs_url=None, redoc_url=None)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    redaction_engine = engine if engine is not None else RedactionEngine()
    sessions: dict[str, ScanSession] = {}
    scan_jobs: dict[str, dict[str, Any]] = {}
    jobs_lock = threading.Lock()

    def _purge_expired_sessions() -> None:
        now = time.time()
        expired = [k for k, s in sessions.items() if now - s.created_at > SESSION_TTL_SECONDS]
        for k in expired:
            del sessions[k]

    def _register_session(session: ScanSession) -> None:
        _purge_expired_sessions()
        if len(sessions) >= 5:
            del sessions[next(iter(sessions))]
        sessions[session.session_id] = session

    async def _read_upload_guarded(file: UploadFile) -> bytes:
        _purge_expired_sessions()
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Die hochgeladene Datei ist leer.")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Datei überschreitet das lokale Limit von 100 MB.")
        return raw

    def _get_session_or_404(session_id: str) -> ScanSession:
        session = sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Sitzung nicht gefunden. Bitte Datei erneut scannen.")
        return session

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        return JSONResponse({
            "offline": True,
            "ner_available": redaction_engine.ner.available,
            "ner_model": redaction_engine.ner.model_name,
            "pdf_available": is_pdf_support_available(),
        })

    @app.post("/api/inspect")
    async def api_inspect(file: UploadFile = File(...)) -> JSONResponse:
        raw = await _read_upload_guarded(file)
        try:
            loaded, pdf_doc = _load_uploaded_source(raw, filename=file.filename or "")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not loaded.headers:
            raise HTTPException(status_code=400, detail="Keine Spaltenüberschriften in der Datei gefunden.")
        is_pdf = pdf_doc is not None
        return JSONResponse({
            "filename": file.filename or ("upload.pdf" if is_pdf else "upload.csv"),
            "file_type": "pdf" if is_pdf else "table",
            "pdf_info": (
                {
                    "page_count": pdf_doc.page_count,
                    "pages_with_text": pdf_doc.pages_with_text,
                    "image_only_pages": pdf_doc.image_only_pages,
                }
                if pdf_doc is not None
                else None
            ),
            "headers": loaded.headers,
            "recommended_columns": ["Text"] if is_pdf else recommend_default_columns(loaded.headers),
            "pii_warning_columns": {} if is_pdf else detect_pii_warning_columns(loaded.headers, loaded.rows),
            "row_count": len(loaded.rows),
            "encoding": loaded.encoding,
            "delimiter": loaded.delimiter,
            "preview_rows": loaded.rows[:3],
        })

    @app.post("/api/scan")
    async def api_scan(file: UploadFile = File(...), options_json: str = Form("{}")) -> JSONResponse:
        raw = await _read_upload_guarded(file)
        try:
            opts: dict[str, Any] = json.loads(options_json) if options_json else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Ungültige Optionen: {exc}") from exc

        try:
            loaded, pdf_doc = _load_uploaded_source(raw, filename=file.filename or "", delimiter=opts.get("delimiter") or None)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not loaded.headers:
            raise HTTPException(status_code=400, detail="Keine Spalten in der Datei gefunden.")

        if pdf_doc is not None:
            opts["columns"] = ["Text"]
        config = _build_config_from_opts(opts)
        ner_by_text: dict[str, list[SpanMatch]] = {}
        redacted_rows, cell_results, summary = redaction_engine.scan_rows(
            headers=loaded.headers,
            rows=loaded.rows,
            config=config,
            encoding=loaded.encoding,
            delimiter=loaded.delimiter,
            out_ner_by_text=ner_by_text,
        )

        cell_lookup = {(c.row_index, c.column): c for c in cell_results}
        residual = run_residual_pii_check(redacted_rows, summary.scanned_columns, config, cell_lookup)

        session = ScanSession(
            session_id=uuid.uuid4().hex,
            filename=file.filename or ("upload.pdf" if pdf_doc is not None else "upload.csv"),
            loaded=loaded,
            config=config,
            redacted_rows=redacted_rows,
            cell_results=cell_results,
            summary=summary,
            cell_lookup=cell_lookup,
            ner_by_text=ner_by_text,
            residual_check=residual,
            pdf_source=pdf_doc,
        )
        _register_session(session)
        return JSONResponse(serialize_session(session))

    @app.post("/api/scan/start")
    async def api_scan_start(file: UploadFile = File(...), options_json: str = Form("{}")) -> JSONResponse:
        raw = await _read_upload_guarded(file)
        try:
            opts: dict[str, Any] = json.loads(options_json) if options_json else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Ungültige Optionen: {exc}") from exc

        filename = file.filename or "upload.csv"
        job_id = uuid.uuid4().hex
        started_at = time.perf_counter()

        with jobs_lock:
            if len(scan_jobs) >= 10:
                del scan_jobs[next(iter(scan_jobs))]
            scan_jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "phase": "loading",
                "phase_label": "Lese Datei ein & erkenne Format/Encoding…",
                "percent": 4,
                "processed": 0,
                "total": 0,
                "redactions_so_far": 0,
                "detail": f"{filename} ({max(1, len(raw) // 1024):,} KB)".replace(",", "."),
                "started_at": started_at,
                "error": None,
                "result": None,
            }

        def _worker() -> None:
            def _on_progress(update: dict[str, Any]) -> None:
                with jobs_lock:
                    job = scan_jobs.get(job_id)
                    if job:
                        job.update(update)

            try:
                loaded, pdf_doc = _load_uploaded_source(raw, filename=filename, delimiter=opts.get("delimiter") or None)
                if not loaded.headers:
                    raise ValueError("Keine Spalten in der Datei gefunden.")

                if pdf_doc is not None:
                    opts["columns"] = ["Text"]
                config = _build_config_from_opts(opts)
                ner_by_text: dict[str, list[SpanMatch]] = {}
                redacted_rows, cell_results, summary = redaction_engine.scan_rows(
                    headers=loaded.headers,
                    rows=loaded.rows,
                    config=config,
                    encoding=loaded.encoding,
                    delimiter=loaded.delimiter,
                    out_ner_by_text=ner_by_text,
                    progress_cb=_on_progress,
                )

                cell_lookup = {(c.row_index, c.column): c for c in cell_results}
                _on_progress({"phase": "finalizing", "phase_label": "2. Kontroll-Durchlauf (Rest-PII-Prüfung) läuft…", "percent": 99})
                residual = run_residual_pii_check(redacted_rows, summary.scanned_columns, config, cell_lookup)

                session = ScanSession(
                    session_id=uuid.uuid4().hex,
                    filename=filename,
                    loaded=loaded,
                    config=config,
                    redacted_rows=redacted_rows,
                    cell_results=cell_results,
                    summary=summary,
                    cell_lookup=cell_lookup,
                    ner_by_text=ner_by_text,
                    residual_check=residual,
                    pdf_source=pdf_doc,
                )
                _register_session(session)
                serialized = serialize_session(session)
                unit_lbl = "Seiten" if pdf_doc is not None else "Zeilen"

                with jobs_lock:
                    job = scan_jobs.get(job_id)
                    if job:
                        job.update({
                            "status": "done",
                            "phase": "done",
                            "phase_label": "Scan abgeschlossen – bereit zur Prüfung!",
                            "percent": 100,
                            "processed": len(loaded.rows),
                            "total": len(loaded.rows),
                            "redactions_so_far": summary.total_redactions,
                            "detail": f"{len(loaded.rows):,} {unit_lbl} · {summary.total_redactions:,} Schwärzungen".replace(",", "."),
                            "result": serialized,
                        })
            except Exception as exc:  # noqa: BLE001
                with jobs_lock:
                    job = scan_jobs.get(job_id)
                    if job:
                        job.update({"status": "error", "phase": "error", "phase_label": "Fehler beim Scannen", "error": str(exc)})

        threading.Thread(target=_worker, daemon=True).start()
        return JSONResponse({"job_id": job_id, "status": "running"})

    @app.get("/api/scan/progress/{job_id}")
    async def api_scan_progress(job_id: str) -> JSONResponse:
        with jobs_lock:
            job = scan_jobs.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="Scan-Auftrag nicht gefunden.")
            snapshot = dict(job)
        snapshot["elapsed_ms"] = int((time.perf_counter() - snapshot.pop("started_at", time.perf_counter())) * 1000)
        return JSONResponse(snapshot)

    @app.get("/api/session/{session_id}/cells")
    async def api_list_cells(
        session_id: str,
        filter: str = Query("all"),
        q: str = Query(""),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> JSONResponse:
        session = _get_session_or_404(session_id)
        page_cells, total = session.get_filtered_cells(filter_mode=filter, query=q, offset=offset, limit=limit)
        context_cols = [c for c in session.loaded.headers if c not in session.summary.scanned_columns]
        row_contexts = {
            str(c.row_index): {col: session.loaded.rows[c.row_index].get(col, "") for col in context_cols}
            for c in page_cells
        }
        return JSONResponse({
            "filter": filter,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page_cells) < total,
            "cells": [c.to_dict() for c in page_cells],
            "row_contexts": row_contexts,
        })

    @app.get("/api/session/{session_id}/table")
    async def api_list_table_rows(
        session_id: str,
        q: str = Query(""),
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ) -> JSONResponse:
        """Liefert paginierte vollständige Zeilen inkl. aller Spalten (auch nicht geschwärzter Spalten)."""
        session = _get_session_or_404(session_id)
        rows_page, total = session.get_table_rows(query=q, offset=offset, limit=limit)
        return JSONResponse({
            "headers": session.loaded.headers,
            "scanned_columns": session.summary.scanned_columns,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(rows_page) < total,
            "rows": rows_page,
        })

    @app.post("/api/session/{session_id}/toggle")
    async def api_toggle_match(session_id: str, req: ToggleMatchRequest) -> JSONResponse:
        session = _get_session_or_404(session_id)
        updated_cell = session.toggle_match(req.match_id, req.redacted)
        if updated_cell is None:
            raise HTTPException(status_code=404, detail="Fundstelle nicht gefunden.")
        return JSONResponse({
            "summary": session.summary.to_dict(),
            "cell": updated_cell.to_dict(),
            "findings_count": len(session.cell_results),
            "revision": session.revision,
        })

    @app.post("/api/session/{session_id}/manual-redact")
    async def api_manual_redact(session_id: str, req: ManualRedactRequest) -> JSONResponse:
        session = _get_session_or_404(session_id)
        try:
            cell, match = session.add_manual_span(req.row_index, req.column, req.start, req.end, expected_text=req.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({
            "summary": session.summary.to_dict(),
            "cell": cell.to_dict(),
            "match": match.to_dict(),
            "findings_count": len(session.cell_results),
            "revision": session.revision,
        })

    @app.post("/api/session/{session_id}/replacement")
    async def api_update_replacement(session_id: str, req: ReplacementUpdateRequest) -> JSONResponse:
        """Ändert den Schwärzungs-Text einer laufenden Sitzung sofort ohne Neuscan."""
        session = _get_session_or_404(session_id)
        session.update_replacement(req.replacement, req.use_typed_placeholders)
        return JSONResponse({
            "replacement": session.config.replacement,
            "use_typed_placeholders": session.config.use_typed_placeholders,
            "revision": session.revision,
        })

    @app.post("/api/session/{session_id}/bulk-word")
    async def api_bulk_word(session_id: str, req: BulkWordActionRequest) -> JSONResponse:
        session = _get_session_or_404(session_id)
        if req.words:
            raw_terms = [w.strip() for w in req.words if w and w.strip()]
        else:
            raw_terms = [
                part.strip()
                for part in req.word.replace("\n", ",").replace(";", ",").split(",")
                if part.strip()
            ]
        # Doppelte Begriffe (case-insensitive) deduplizieren
        terms: list[str] = []
        seen_l: set[str] = set()
        for t in raw_terms:
            tl = t.lower()
            if tl not in seen_l:
                seen_l.add(tl)
                terms.append(t)

        if not terms:
            raise HTTPException(status_code=400, detail="Bitte mindestens ein Wort angeben.")

        if req.action == "release":
            existing_wl = {w.lower() for w in session.config.extra_whitelist}
            for t in terms:
                if t.lower() not in existing_wl:
                    session.config.extra_whitelist.append(t)
                    existing_wl.add(t.lower())
            session.config.extra_blacklist = [w for w in session.config.extra_blacklist if w.lower() not in seen_l]
            for rec in session.manual_spans:
                if rec.text.strip().lower() in seen_l:
                    rec.redacted = False
        elif req.action == "redact":
            existing_bl = {w.lower() for w in session.config.extra_blacklist}
            for t in terms:
                if t.lower() not in existing_bl:
                    session.config.extra_blacklist.append(t)
                    existing_bl.add(t.lower())
            session.config.extra_whitelist = [w for w in session.config.extra_whitelist if w.lower() not in seen_l]
        else:
            raise HTTPException(status_code=400, detail="Unbekannte Aktion.")

        for key in list(session.toggle_overrides.keys()):
            r_idx, col, s, e = key
            orig = session.loaded.rows[r_idx].get(col, "") or ""
            if orig[s:e].strip().lower() in seen_l:
                del session.toggle_overrides[key]

        affected_count = session.apply_bulk_words_incremental(words=terms, action=req.action, engine=redaction_engine)
        payload = serialize_session(session)
        payload["affected_count"] = affected_count
        payload["terms_count"] = len(terms)
        return JSONResponse(payload)

    @app.delete("/api/session/{session_id}")
    async def api_delete_session(session_id: str) -> JSONResponse:
        return JSONResponse({"deleted": sessions.pop(session_id, None) is not None})

    @app.get("/api/session/{session_id}/pdf-page/{page_num}")
    async def api_pdf_page_preview(
        session_id: str,
        page_num: int,
        mode: str = Query("redacted"),
    ) -> Response:
        """Rendert eine einzelne PDF-Seite (Original oder live geschwärzt) als PNG für die Layout-Vorschau."""
        session = _get_session_or_404(session_id)
        if session.pdf_source is None:
            raise HTTPException(status_code=400, detail="Diese Sitzung stammt nicht aus einem PDF-Upload.")
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= session.pdf_source.page_count:
            raise HTTPException(status_code=404, detail=f"PDF-Seite {page_num} existiert nicht.")
        clean_mode = "original" if mode == "original" else "redacted"
        cell = session.cell_lookup.get((page_idx, "Text"))
        try:
            png_bytes = render_pdf_page_png(
                pdf_doc=session.pdf_source,
                page_index=page_idx,
                cell=cell,
                config=session.config,
                mode=clean_mode,
                dpi=130,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(png_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/session/{session_id}/export/{fmt}")
    async def api_export(session_id: str, fmt: str) -> Response:
        session = _get_session_or_404(session_id)
        stem = Path(session.filename).stem

        if fmt == "csv":
            data = dump_csv_bytes(session.loaded.headers, session.redacted_rows, delimiter=session.loaded.delimiter, encoding="utf-8-sig")
            return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{stem}_geschwaerzt.csv"'})
        if fmt == "pdf":
            if session.pdf_source is not None:
                data = apply_pdf_redactions_to_bytes(session.pdf_source, session.cell_results, session.config)
            else:
                data = dump_pdf_bytes(session.loaded.headers, session.redacted_rows, filename=session.filename, summary=session.summary, config=session.config)
            return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{stem}_geschwaerzt.pdf"'})
        if fmt == "xlsx":
            data = dump_xlsx_bytes(session.loaded.headers, session.redacted_rows)
            return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{stem}_geschwaerzt.xlsx"'})
        if fmt == "report":
            html_str = build_html_report(session.filename, session.summary, session.cell_results, session.config)
            return Response(html_str.encode("utf-8"), media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="INTERN_NICHT_VERSENDEN_{stem}_pruefbericht.html"'})
        if fmt == "audit":
            data = build_audit_csv_bytes(session.cell_results, include_originals=True)
            return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="INTERN_NICHT_VERSENDEN_{stem}_audit_log.csv"'})
        if fmt == "audit-anon":
            data = build_audit_csv_bytes(session.cell_results, include_originals=False)
            return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{stem}_audit_ohne_originale.csv"'})

        raise HTTPException(status_code=400, detail=f"Unbekanntes Export-Format: {fmt}")

    return app


def _is_port_free(host: str, port: int) -> bool:
    for family, addr in ((socket.AF_INET, host), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.15)
                if probe.connect_ex((addr, port)) == 0:
                    return False
        except OSError:
            pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


def _find_free_port(host: str = "127.0.0.1", preferred_port: int = 8765, max_tries: int = 50) -> int:
    for candidate in range(preferred_port, preferred_port + max_tries):
        if _is_port_free(host, candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def run_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True, allow_network: bool = False) -> None:
    import sys
    import webbrowser
    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_network:
        print(
            f"\nSicherheits-Sperre: Bindung an '{host}' (Netzwerk) ist ohne Authentifizierung blockiert.\n"
            "Nutze den sicheren Lokal-Standard (127.0.0.1) oder explizit --allow-network.\n",
            file=sys.stderr,
        )
        raise SystemExit(2)

    actual_port = _find_free_port(host, port)
    if actual_port != port:
        print(f"\nHinweis: Port {port} ist bereits durch eine andere App belegt – weiche automatisch auf freien Port {actual_port} aus.")

    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{actual_port}"
    if open_browser:
        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"\nBLACKTHIS läuft 100 % lokal unter: {url}")
    print("Zum Beenden im Terminal Strg+C drücken.\n")
    uvicorn.run(create_app(), host=host, port=actual_port, log_level="warning")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BLACKTHIS – lokale DSGVO-Schwärzungsoberfläche")
    parser.add_argument("--host", default="127.0.0.1", help="Bind-Host (Standard: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bevorzugter Port (Standard: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")
    parser.add_argument("--allow-network", action="store_true", help="Erlaubt explizit Bindung außerhalb von localhost.")
    cli_args = parser.parse_args()
    run_server(host=cli_args.host, port=cli_args.port, open_browser=not cli_args.no_open, allow_network=cli_args.allow_network)
