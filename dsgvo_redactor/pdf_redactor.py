"""Lokale In-Place PDF-Einlesung & unwiderrufliche DSGVO-Schwärzung via PyMuPDF (100 % offline)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .csv_io import LoadedCSV
from .models import CellResult, RedactionConfig

_BLOCK_CHARS = frozenset("█▓▒░■▪● ")


def is_pdf_bytes(raw: bytes, filename: str = "") -> bool:
    """Erkennt zuverlässig anhand Dateiendung oder PDF-Header (%PDF-), ob es sich um ein PDF handelt."""
    if filename.lower().endswith(".pdf"):
        return True
    return raw.lstrip()[:5] == b"%PDF-"


def is_pdf_support_available() -> bool:
    """Prüft ohne Seiteneffekte, ob PyMuPDF (fitz) lokal installiert ist."""
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def _import_fitz() -> Any:
    try:
        import fitz
        return fitz
    except ImportError as exc:
        raise RuntimeError(
            "Für den PDF-Upload wird das lokale Paket 'pymupdf' benötigt. "
            "Bitte einmalig im Terminal installieren: .venv/bin/python -m pip install pymupdf"
        ) from exc


@dataclass
class PdfPageLayout:
    """Extrahierter Text einer PDF-Seite inkl. exakter Zeichen-Koordinaten (1:1-Mapping)."""

    page_index: int
    width: float
    height: float
    text: str
    char_bboxes: list[tuple[float, float, float, float] | None]
    has_text: bool
    has_images: bool


@dataclass
class PdfDocumentData:
    """Eingelesenes PDF-Dokument inkl. Original-Bytes für verlustfreien Layout-Export."""

    raw_bytes: bytes
    page_count: int
    pages: list[PdfPageLayout]
    image_only_pages: list[int]  # 1-basierte Seitenzahlen ohne durchsuchbaren Text (reine Scans)
    pages_with_text: int
    loaded: LoadedCSV


def extract_pdf_document(raw_bytes: bytes) -> PdfDocumentData:
    """Liest ein PDF zeichengenau inkl. Bounding-Boxes ein und erzeugt eine seitenweise Tabellen-Sicht."""
    fitz = _import_fitz()
    try:
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"PDF konnte nicht geöffnet werden: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError(
                "Das PDF ist passwortgeschützt und kann ohne Entsperrung nicht verarbeitet werden."
            )
        if len(doc) == 0:
            raise ValueError("Das hochgeladene PDF enthält keine Seiten.")

        pages: list[PdfPageLayout] = []
        image_only_pages: list[int] = []
        rows: list[dict[str, str]] = []

        for page_idx, page in enumerate(doc):
            raw_dict = page.get_text("rawdict", sort=True)
            chars: list[str] = []
            bboxes: list[tuple[float, float, float, float] | None] = []

            for block in raw_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                if chars and chars[-1] != "\n":
                    chars.append("\n")
                    bboxes.append(None)

                for line_idx, line in enumerate(block.get("lines", [])):
                    if line_idx > 0 and chars and chars[-1] != "\n":
                        chars.append("\n")
                        bboxes.append(None)

                    prev_x1: float | None = None
                    for span in line.get("spans", []):
                        span_chars = span.get("chars") or []
                        if not span_chars:
                            continue
                        font_size = float(span.get("size") or 10.0)
                        first_bbox = span_chars[0].get("bbox")
                        first_ch = (span_chars[0].get("c") or "").replace("\u00a0", " ")
                        if (
                            prev_x1 is not None
                            and first_bbox is not None
                            and len(first_bbox) == 4
                            and (float(first_bbox[0]) - prev_x1) > max(1.5, font_size * 0.22)
                            and chars
                            and chars[-1] not in (" ", "\n", "\t")
                            and not first_ch.startswith((" ", "\t", "\n"))
                        ):
                            chars.append(" ")
                            bboxes.append(None)

                        for c in span_chars:
                            ch_raw = c.get("c", "")
                            if not ch_raw:
                                continue
                            ch_norm = ch_raw.replace("\r", "").replace("\u00a0", " ")
                            if not ch_norm:
                                continue
                            raw_bb = c.get("bbox")
                            bb: tuple[float, float, float, float] | None = (
                                (float(raw_bb[0]), float(raw_bb[1]), float(raw_bb[2]), float(raw_bb[3]))
                                if raw_bb and len(raw_bb) == 4
                                else None
                            )
                            for single in ch_norm:
                                chars.append(single)
                                bboxes.append(bb)
                            if bb is not None:
                                prev_x1 = bb[2]

            page_text = "".join(chars)
            has_text = bool(page_text.strip())
            has_images = bool(page.get_images(full=False))
            if not has_text:
                image_only_pages.append(page_idx + 1)

            rect = page.rect
            pages.append(
                PdfPageLayout(
                    page_index=page_idx,
                    width=float(rect.width),
                    height=float(rect.height),
                    text=page_text,
                    char_bboxes=bboxes,
                    has_text=has_text,
                    has_images=has_images,
                )
            )
            rows.append({"Seite": f"Seite {page_idx + 1}", "Text": page_text})

        pages_with_text = sum(1 for p in pages if p.has_text)
        loaded = LoadedCSV(
            headers=["Seite", "Text"],
            rows=rows,
            encoding="pdf",
            delimiter=";",
        )
        return PdfDocumentData(
            raw_bytes=raw_bytes,
            page_count=len(pages),
            pages=pages,
            image_only_pages=image_only_pages,
            pages_with_text=pages_with_text,
            loaded=loaded,
        )
    finally:
        doc.close()


def _merge_span_bboxes(
    char_bboxes: list[tuple[float, float, float, float] | None],
    start: int,
    end: int,
) -> list[tuple[float, float, float, float]]:
    """Fasst zusammenhängende Zeichen-Bounding-Boxes je visueller Zeile zu präzisen Schwärzungs-Rechtecken zusammen."""
    valid: list[tuple[float, float, float, float]] = []
    slice_end = min(end, len(char_bboxes))
    for bb in char_bboxes[max(0, start) : slice_end]:
        if bb is not None and bb[2] > bb[0] and bb[3] > bb[1]:
            valid.append(bb)
    if not valid:
        return []

    merged: list[list[float]] = []
    for x0, y0, x1, y1 in valid:
        if not merged:
            merged.append([x0, y0, x1, y1])
            continue
        cur = merged[-1]
        cur_h = max(1.0, cur[3] - cur[1])
        cur_cy = (cur[1] + cur[3]) * 0.5
        new_cy = (y0 + y1) * 0.5
        same_line = abs(new_cy - cur_cy) <= max(4.0, cur_h * 0.55) and x0 >= cur[0] - 4.0
        if same_line:
            cur[0] = min(cur[0], x0)
            cur[1] = min(cur[1], y0)
            cur[2] = max(cur[2], x1)
            cur[3] = max(cur[3], y1)
        else:
            merged.append([x0, y0, x1, y1])

    # Vertikal minimal einrücken, damit eng gesetzte Nachbarzeilen nicht versehentlich gelöscht werden
    rects: list[tuple[float, float, float, float]] = []
    for x0, y0, x1, y1 in merged:
        h = y1 - y0
        inset_y = min(1.0, h * 0.08) if h > 4.0 else 0.0
        ry0 = y0 + inset_y
        ry1 = y1 - inset_y
        if ry1 <= ry0 + 0.5:
            ry0, ry1 = y0, y1
        rects.append((max(0.0, x0 - 0.6), ry0, x1 + 0.6, ry1))
    return rects


def _resolve_pdf_redaction_label(category_value: str, config: RedactionConfig) -> str:
    """Ermittelt die optionale weiße Aufschrift auf dem schwarzen PDF-Schwärzungsbalken."""
    if config.use_typed_placeholders:
        return f"[{category_value}]"
    repl = (config.replacement or "").strip()
    if not repl or all(ch in _BLOCK_CHARS for ch in repl):
        return ""
    return repl


def _redact_fitz_page(
    page: Any,
    layout: PdfPageLayout,
    cell: CellResult,
    config: RedactionConfig,
    fitz: Any,
) -> int:
    """Trägt alle aktiven Schwärzungen einer Seite ein, löscht überlappende Links und wendet sie unwiderruflich an."""
    redaction_rects: list[Any] = []
    applied_matches = 0

    for m in cell.matches:
        if not m.redacted or m.protected:
            continue
        boxes = _merge_span_bboxes(layout.char_bboxes, m.start, m.end)
        if not boxes:
            continue
        applied_matches += 1
        label = _resolve_pdf_redaction_label(m.category.value, config)
        for idx, (x0, y0, x1, y1) in enumerate(boxes):
            rect = fitz.Rect(x0, y0, x1, y1)
            redaction_rects.append(rect)
            rect_text = label if idx == 0 else ""
            fontsize = max(5.5, min(10.0, rect.height * 0.72))
            page.add_redact_annot(
                rect,
                text=rect_text,
                fontname="helv",
                fontsize=fontsize,
                align=fitz.TEXT_ALIGN_CENTER,
                fill=(0, 0, 0),
                text_color=(1, 1, 1),
                cross_out=False,
            )

    if not redaction_rects:
        return 0

    # Klickbare Link-Annotationen (z. B. mailto:, tel:, URLs) im geschwärzten Bereich mit entfernen!
    for link in page.get_links():
        link_from = link.get("from")
        if not link_from:
            continue
        link_rect = fitz.Rect(link_from)
        if any(link_rect.intersects(r) for r in redaction_rects):
            try:
                page.delete_link(link)
            except Exception:  # noqa: BLE001
                pass

    # Text physisch aus dem PDF-Content-Stream entfernen; Hintergrundbilder/Briefköpfe bleiben erhalten
    try:
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
    except TypeError:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    return applied_matches


def apply_pdf_redactions_to_bytes(
    pdf_doc: PdfDocumentData,
    cell_results: list[CellResult],
    config: RedactionConfig,
) -> bytes:
    """Wendet alle aktiven Schwärzungen unwiderruflich direkt im Original-PDF an und bereinigt Metadaten."""
    fitz = _import_fitz()
    doc = fitz.open(stream=pdf_doc.raw_bytes, filetype="pdf")
    try:
        cells_by_page = {
            c.row_index: c
            for c in cell_results
            if c.column == "Text" and 0 <= c.row_index < len(pdf_doc.pages)
        }
        for page_idx, cell in cells_by_page.items():
            _redact_fitz_page(doc[page_idx], pdf_doc.pages[page_idx], cell, config, fitz)

        # Dokument-Metadaten DSGVO-konform bereinigen (kein Autor/Ersteller/Titel/Original-Dateiname)
        doc.set_metadata(
            {
                "title": "",
                "author": "",
                "subject": "",
                "keywords": "",
                "creator": "",
                "producer": "",
            }
        )
        try:
            doc.del_xml_metadata()
        except Exception:  # noqa: BLE001
            pass

        return bytes(doc.tobytes(garbage=4, deflate=True, clean=True))
    finally:
        doc.close()


def render_pdf_page_png(
    pdf_doc: PdfDocumentData,
    page_index: int,
    cell: CellResult | None,
    config: RedactionConfig,
    mode: str = "redacted",
    dpi: int = 130,
) -> bytes:
    """Rendert eine einzelne PDF-Seite (Original oder live geschwärzt) als PNG für die Browser-Vorschau."""
    if page_index < 0 or page_index >= len(pdf_doc.pages):
        raise ValueError(f"Ungültige PDF-Seite: {page_index + 1}")
    fitz = _import_fitz()
    doc = fitz.open(stream=pdf_doc.raw_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        if mode == "redacted" and cell is not None:
            _redact_fitz_page(page, pdf_doc.pages[page_index], cell, config, fitz)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return bytes(pix.tobytes("png"))
    finally:
        doc.close()
