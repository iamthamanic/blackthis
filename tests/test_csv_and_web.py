"""End-to-End Tests für CSV-Encoding/Multiline, CLI, Excel/HTML Export und FastAPI Web-UI."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dsgvo_redactor.cli import main as cli_main
from dsgvo_redactor.csv_io import dump_csv_bytes, load_csv_bytes
from dsgvo_redactor.web_app import _find_free_port, create_app

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "beispiel_dsgvo.csv"


def test_csv_multiline_and_cp1252_roundtrip():
    raw_text = 'Datum;Notiz\r\n08.04.2026;"Sehr geehrter Herr Müller,\r\nCurtiusstraße 6, 50935 Köln"\r\n'
    loaded = load_csv_bytes(raw_text.encode("cp1252"))
    assert loaded.encoding == "cp1252"
    assert loaded.delimiter == ";"
    assert len(loaded.rows) == 1
    assert "Müller" in loaded.rows[0]["Notiz"]
    assert "\n" in loaded.rows[0]["Notiz"]

    out_bytes = dump_csv_bytes(loaded.headers, loaded.rows, delimiter=";")
    reloaded = load_csv_bytes(out_bytes)
    assert reloaded.rows[0]["Notiz"] == loaded.rows[0]["Notiz"]


def test_cli_end_to_end(tmp_path: Path):
    out_csv = tmp_path / "out.csv"
    out_html = tmp_path / "report.html"
    out_xlsx = tmp_path / "out.xlsx"
    out_audit = tmp_path / "audit.csv"

    rc = cli_main([
        "--input", str(SAMPLE_CSV),
        "--vorname", "Thomas",
        "--nachname", "Mustermann",
        "--output", str(out_csv),
        "--report", str(out_html),
        "--xlsx", str(out_xlsx),
        "--audit-csv", str(out_audit),
    ])
    assert rc == 0
    assert out_csv.is_file()
    assert out_html.is_file()
    assert out_xlsx.is_file()
    assert out_audit.is_file()

    content = out_csv.read_text(encoding="utf-8-sig")
    # Antragsteller geschützt
    assert "Herr Mustermann" in content
    # Sensible Daten Dritter konsequent geschwärzt
    for forbidden in (
        "Sabine Becker",
        "Lukas Weber",
        "DE89 3704 0044 0532 0130 00",
        "COBADEFFXXX",
        "149,50",
        "Sachbearbeiterin",
        "Claudia Hoffmann",
        "Curtiusstr. 6",
        "50935 Köln",
        "Kowalski",
        "Stefan Klein",
        "Herr Schneider",
        "14.03.1984",
        "0171 9876543",
        "stefan.schneider@beispielmail.de",
        "Disponent",
        "Markus Vogel",
        "210,00 EUR",
        "Julia Bergmann",
        "Projektleiterin",
        "Venloer Str. 214a",
        "50823 Köln",
        "+49 221 5551234",
        "www.musterumzug-koeln.de",
        "DE123456789",
        "K-AB 4821",
    ):
        assert forbidden not in content, f"Sensible Daten geleakt: {forbidden}"

    # Wichtige harmlose Daten bleiben erhalten!
    for must_keep in (
        "Stadt Köln",
        "21.04.2026",
        "7,5t",
        "3,5 Tonnen",
        "HV123-396094",
        "Outlook-pspeh4mm.jpg",
        "RheinLogistik Umzüge GmbH",  # Firmennamen standardmäßig AUS!
    ):
        assert must_keep in content, f"Harmloser Wert fälschlich geschwärzt: {must_keep}"


def test_web_app_inspect_scan_toggle_and_export():
    app = create_app()
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    status = client.get("/api/status").json()
    assert status["offline"] is True

    csv_bytes = SAMPLE_CSV.read_bytes()
    ins = client.post("/api/inspect", files={"file": ("beispiel_dsgvo.csv", csv_bytes, "text/csv")})
    assert ins.status_code == 200
    ins_data = ins.json()
    assert ins_data["recommended_columns"] == ["Notiz"]
    assert ins_data["row_count"] == 12

    opts = {
        "applicant_first_name": "Thomas",
        "applicant_last_name": "Mustermann",
        "columns": ["Notiz"],
        "replacement": "██████",
        "redact_companies": False,
    }
    scan = client.post(
        "/api/scan",
        files={"file": ("beispiel_dsgvo.csv", csv_bytes, "text/csv")},
        data={"options_json": json.dumps(opts)},
    )
    assert scan.status_code == 200
    scan_data = scan.json()
    sid = scan_data["session_id"]
    assert scan_data["summary"]["total_redactions"] >= 20
    assert scan_data["summary"]["protected_applicant_hits"] >= 1

    # 1-Klick-Toggle testen
    first_redacted_match = next(
        m for c in scan_data["cells"] for m in c["matches"] if m["redacted"] and not m["protected"]
    )
    tog = client.post(
        f"/api/session/{sid}/toggle",
        json={"match_id": first_redacted_match["id"], "redacted": False},
    )
    assert tog.status_code == 200
    assert tog.json()["summary"]["total_redactions"] == scan_data["summary"]["total_redactions"] - 1

    # Alle 5 Exporte testen (inkl. PDF mit echten Schwärzungsbalken)
    for fmt in ("csv", "pdf", "xlsx", "report", "audit"):
        resp = client.get(f"/api/session/{sid}/export/{fmt}")
        assert resp.status_code == 200
        assert len(resp.content) > 100
        if fmt == "pdf":
            assert resp.headers["content-type"].startswith("application/pdf")
            assert resp.content.startswith(b"%PDF-1.4")
            assert b"%%EOF" in resp.content[-32:]
            assert b"Antragsteller:" not in resp.content
            assert b"BLACKTHIS" not in resp.content


def test_web_app_manual_mouse_redaction_all_rows_and_bulk_persistence():
    app = create_app()
    client = TestClient(app)

    csv_text = (
        "Datum;Notiz\n"
        "01.04.2026;Der Auftrag wurde geprüft: Bestellnummer HV123-396094 liegt vor.\n"
        "02.04.2026;Die Bearbeitung ist abgeschlossen, der Bescheid bleibt offen.\n"
    )
    opts = {
        "applicant_first_name": "Max",
        "applicant_last_name": "Mustermann",
        "columns": ["Notiz"],
        "replacement": "██████",
    }
    scan = client.post(
        "/api/scan",
        files={"file": ("clean.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"options_json": json.dumps(opts)},
    )
    assert scan.status_code == 200
    scan_data = scan.json()
    sid = scan_data["session_id"]
    assert scan_data["summary"]["total_redactions"] == 0
    assert scan_data["total_scanned_cells"] == 2

    # Tab „Alle Zeilen“ liefert auch 0-Treffer-Zeilen
    all_resp = client.get(f"/api/session/{sid}/cells?filter=all")
    assert all_resp.status_code == 200
    all_data = all_resp.json()
    assert all_data["total"] == 2
    assert len(all_data["cells"]) == 2
    row0 = all_data["cells"][0]
    target = "HV123-396094"
    pos = row0["original"].index(target)

    # Nachträgliche Einzel-Schwärzung per Maus-Markierung („Nur hier schwärzen“)
    man = client.post(
        f"/api/session/{sid}/manual-redact",
        json={
            "row_index": 0,
            "column": "Notiz",
            "start": pos,
            "end": pos + len(target),
            "text": target,
        },
    )
    assert man.status_code == 200
    man_data = man.json()
    assert man_data["summary"]["total_redactions"] == 1
    assert target not in man_data["cell"]["redacted"]
    assert "██████" in man_data["cell"]["redacted"]
    assert man_data["match"]["category"] == "MANUELL"

    # CSV- und HTML-Export enthalten sofort die manuelle Schwärzung
    csv_out = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert target not in csv_out
    report_out = client.get(f"/api/session/{sid}/export/report").content.decode("utf-8")
    assert "badge-manual" in report_out

    # „Überall schwärzen“ für anderes Wort verliert NICHT die bisherige Einzel-Markierung!
    bulk = client.post(
        f"/api/session/{sid}/bulk-word",
        json={"word": "Bescheid", "action": "redact"},
    )
    assert bulk.status_code == 200
    bulk_data = bulk.json()
    assert bulk_data["summary"]["total_redactions"] == 2
    csv_after_bulk = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert target not in csv_after_bulk
    assert "Bescheid" not in csv_after_bulk

    # Einzel-Markierung lässt sich per 1 Klick wieder rückgängig machen
    manual_match_id = man_data["match"]["id"]
    undo = client.post(
        f"/api/session/{sid}/toggle",
        json={"match_id": manual_match_id, "redacted": False},
    )
    assert undo.status_code == 200
    assert target in undo.json()["cell"]["redacted"]
    csv_after_undo = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert target in csv_after_undo
    assert "Bescheid" not in csv_after_undo


def test_web_app_manual_redaction_with_crlf_multiline_csv():
    app = create_app()
    client = TestClient(app)

    csv_text = 'Datum;Notiz\r\n01.04.2026;"Zeile eins\r\nZeile zwei\r\nGeheimcode XYZ-777"\r\n'
    scan = client.post(
        "/api/scan",
        files={"file": ("crlf.csv", csv_text.encode("cp1252"), "text/csv")},
        data={"options_json": json.dumps({"columns": ["Notiz"]})},
    )
    assert scan.status_code == 200
    sid = scan.json()["session_id"]

    all_cells = client.get(f"/api/session/{sid}/cells?filter=all").json()["cells"]
    orig = all_cells[0]["original"]
    assert "\r" not in orig  # CRLF sauber auf \n normalisiert
    pos = orig.index("XYZ-777")
    man = client.post(
        f"/api/session/{sid}/manual-redact",
        json={"row_index": 0, "column": "Notiz", "start": pos, "end": pos + 7, "text": "XYZ-777"},
    )
    assert man.status_code == 200
    assert "XYZ-777" not in man.json()["cell"]["redacted"]
    assert "██████" in man.json()["cell"]["redacted"]


def test_find_free_port_avoids_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]
        chosen = _find_free_port("127.0.0.1", preferred_port=busy_port, max_tries=20)
        assert chosen != busy_port
        assert chosen > busy_port


def test_web_app_async_scan_start_and_live_progress():
    app = create_app()
    client = TestClient(app)

    csv_bytes = SAMPLE_CSV.read_bytes()
    start = client.post(
        "/api/scan/start",
        files={"file": ("beispiel_dsgvo.csv", csv_bytes, "text/csv")},
        data={"options_json": json.dumps({"applicant_first_name": "Thomas", "applicant_last_name": "Mustermann", "columns": ["Notiz"]})},
    )
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    final = None
    for _ in range(100):
        prog = client.get(f"/api/scan/progress/{job_id}")
        assert prog.status_code == 200
        data = prog.json()
        assert data["status"] in ("running", "done")
        if data["status"] == "done":
            final = data
            break
        time.sleep(0.05)

    assert final is not None
    assert final["percent"] == 100
    assert final["result"]["summary"]["total_redactions"] >= 20
    assert final["result"]["session_id"]
    assert final["result"]["residual_check"]["count"] == 0


def test_smart_pii_column_selection_xlsx_import_audit_anon_and_session_delete():
    from dsgvo_redactor.csv_io import dump_xlsx_bytes

    app = create_app()
    client = TestClient(app)

    headers = ["Datum", "Vorname", "E-Mail", "Notiz"]
    rows = [
        {
            "Datum": "01.04.2026",
            "Vorname": "Sabine",
            "E-Mail": "sabine.becker@example.de",
            "Notiz": "Bitte Max Mustermann (max.own@firma.de) und 030 / 1234 5678 prüfen.",
        }
    ]
    xlsx_bytes = dump_xlsx_bytes(headers, rows)

    # Excel-Upload + automatische PII-Spalten-Erkennung
    ins = client.post(
        "/api/inspect",
        files={"file": ("crm_export.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert ins.status_code == 200
    ins_data = ins.json()
    assert set(ins_data["recommended_columns"]) == {"Vorname", "E-Mail", "Notiz"}
    assert "E-Mail" in ins_data["pii_warning_columns"]

    # Scan mit Antragsteller-Zusatzdaten (eigene E-Mail bleibt geschützt)
    opts = {
        "applicant_first_name": "Max",
        "applicant_last_name": "Mustermann",
        "applicant_extra_data": "max.own@firma.de",
        "columns": ins_data["recommended_columns"],
    }
    scan = client.post(
        "/api/scan",
        files={"file": ("crm_export.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"options_json": json.dumps(opts)},
    )
    assert scan.status_code == 200
    sdata = scan.json()
    sid = sdata["session_id"]
    assert sdata["residual_check"]["count"] == 0

    csv_out = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert "sabine.becker@example.de" not in csv_out
    assert "max.own@firma.de" in csv_out

    # Warn-Dateiname & Banner im internen Prüfbericht + Audit ohne Originale
    rep = client.get(f"/api/session/{sid}/export/report")
    assert "INTERN_NICHT_VERSENDEN_" in rep.headers["content-disposition"]
    assert "NIEMALS an den Antragsteller versenden" in rep.text

    audit_anon = client.get(f"/api/session/{sid}/export/audit-anon")
    assert audit_anon.status_code == 200
    assert "Original_Text" not in audit_anon.text
    assert "sabine.becker@example.de" not in audit_anon.text

    # Sitzung sauber aus RAM löschen
    del_resp = client.delete(f"/api/session/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True
    assert client.get(f"/api/session/{sid}/export/csv").status_code == 404


def test_releasing_review_match_removes_it_from_review_list():
    app = create_app()
    client = TestClient(app)

    csv_text = "Datum;Notiz\n01.04.2026;Bitte bei Xantener kurz nachfragen.\n"
    scan = client.post(
        "/api/scan",
        files={"file": ("review.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"options_json": json.dumps({"columns": ["Notiz"], "use_ner": False})},
    )
    assert scan.status_code == 200
    sdata = scan.json()
    sid = sdata["session_id"]
    assert sdata["summary"]["review_findings"] == 1

    review_match = sdata["cells"][0]["matches"][0]
    tog = client.post(
        f"/api/session/{sid}/toggle",
        json={"match_id": review_match["id"], "redacted": False},
    )
    assert tog.status_code == 200
    assert tog.json()["summary"]["review_findings"] == 0

    rem = client.get(f"/api/session/{sid}/cells?filter=review").json()
    assert rem["total"] == 0


def test_live_replacement_update_and_multi_word_bulk_release():
    app = create_app()
    client = TestClient(app)

    csv_text = (
        "Datum;Notiz\n"
        "01.04.2026;Bitte bei Xantener und bei Ypsilona nachfragen.\n"
        "02.04.2026;Rückruf an Frau Sabine Becker.\n"
    )
    scan = client.post(
        "/api/scan",
        files={"file": ("multi.csv", csv_text.encode("utf-8"), "text/csv")},
        data={"options_json": json.dumps({"columns": ["Notiz"], "use_ner": False, "replacement": "██████"})},
    )
    assert scan.status_code == 200
    sid = scan.json()["session_id"]

    # 1. Schwärzungs-Text im Nachhinein live ändern (ohne Neuscan)
    upd = client.post(
        f"/api/session/{sid}/replacement",
        json={"replacement": "[SECRET]", "use_typed_placeholders": False},
    )
    assert upd.status_code == 200
    csv_out = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert "[SECRET]" in csv_out
    assert "██████" not in csv_out

    # 2. Mehrere Wörter kommagetrennt auf einmal überall freigeben
    rel = client.post(
        f"/api/session/{sid}/bulk-word",
        json={"word": "Xantener, Ypsilona", "action": "release"},
    )
    assert rel.status_code == 200
    rel_data = rel.json()
    assert rel_data["terms_count"] == 2
    assert rel_data["summary"]["review_findings"] == 0
    csv_after = client.get(f"/api/session/{sid}/export/csv").content.decode("utf-8-sig")
    assert "Xantener" in csv_after
    assert "Ypsilona" in csv_after
    assert "Sabine Becker" not in csv_after

    # 3. Tabellen-Ansicht liefert ALLE Spalten inkl. Spaltenkopf (gescannte & ungescannte)
    tbl = client.get(f"/api/session/{sid}/table")
    assert tbl.status_code == 200
    tdata = tbl.json()
    assert tdata["headers"] == ["Datum", "Notiz"]
    assert tdata["total"] == 2
    assert tdata["rows"][0]["original"]["Datum"] == "01.04.2026"
    assert tdata["rows"][1]["redacted"]["Datum"] == "02.04.2026"
    assert "Sabine Becker" not in tdata["rows"][1]["redacted"]["Notiz"]


def test_pdf_upload_inplace_redaction_and_preview():
    import pytest

    fitz = pytest.importorskip("fitz")

    # 2-seitiges Test-PDF im Speicher erzeugen: Seite 1 Text + mailto-Link, Seite 2 leer (Scan-Simulation)
    src_doc = fitz.open()
    src_doc.set_metadata({"author": "Geheimer Autor", "creator": "Internes CRM"})
    p1 = src_doc.new_page()
    p1.insert_text((50, 80), "DSGVO Auskunft fuer Thomas Mustermann")
    p1.insert_text((50, 120), "Ansprechpartnerin: Frau Sabine Becker")
    p1.insert_text((50, 160), "IBAN: DE89 3704 0044 0532 0130 00")
    p1.insert_text((50, 200), "Kontakt: sabine.becker@example.de")
    p1.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(90, 185, 290, 210), "uri": "mailto:sabine.becker@example.de"})
    src_doc.new_page()  # Seite 2 ohne Text
    pdf_bytes = src_doc.tobytes()
    src_doc.close()

    app = create_app()
    client = TestClient(app)

    # 1. Inspect erkennt PDF + leere/gescannte Seite 2
    ins = client.post("/api/inspect", files={"file": ("vertrag.pdf", pdf_bytes, "application/pdf")})
    assert ins.status_code == 200
    idata = ins.json()
    assert idata["file_type"] == "pdf"
    assert idata["pdf_info"]["page_count"] == 2
    assert idata["pdf_info"]["pages_with_text"] == 1
    assert idata["pdf_info"]["image_only_pages"] == [2]

    # 2. Scan schwärzt Dritte und schützt Antragsteller Thomas Mustermann
    opts = {
        "applicant_first_name": "Thomas",
        "applicant_last_name": "Mustermann",
        "use_ner": False,
        "replacement": "██████",
    }
    scan = client.post(
        "/api/scan",
        files={"file": ("vertrag.pdf", pdf_bytes, "application/pdf")},
        data={"options_json": json.dumps(opts)},
    )
    assert scan.status_code == 200
    sdata = scan.json()
    sid = sdata["session_id"]
    assert sdata["file_type"] == "pdf"
    assert sdata["summary"]["total_redactions"] >= 3
    assert sdata["summary"]["protected_applicant_hits"] >= 1

    # 3. Visuelle PDF-Seiten-Vorschau liefert PNG
    prev = client.get(f"/api/session/{sid}/pdf-page/1?mode=redacted")
    assert prev.status_code == 200
    assert prev.headers["content-type"].startswith("image/png")
    assert prev.content.startswith(b"\x89PNG")

    # 4. Echter In-Place PDF-Export löscht Text, mailto-Link & Metadaten unwiderruflich
    exp = client.get(f"/api/session/{sid}/export/pdf")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/pdf")

    out_doc = fitz.open(stream=exp.content, filetype="pdf")
    try:
        assert len(out_doc) == 2
        extracted = out_doc[0].get_text("text")
        assert "Thomas Mustermann" in extracted
        assert "Sabine Becker" not in extracted
        assert "DE89" not in extracted
        assert "sabine.becker@example.de" not in extracted
        assert out_doc[0].get_links() == []
        meta = out_doc.metadata or {}
        assert meta.get("author", "") == ""
        assert meta.get("creator", "") == ""
        assert meta.get("title", "") == ""
    finally:
        out_doc.close()


def test_csv_delimiter_detection_with_sql_queries_and_json_in_last_column():
    header = "Datum;Aktion;Art;Beschreibung;Tabelle;Datensatz;Bestellung;Rechnung;Kunde;Alte Daten;Neue Daten\r\n"

    # 1. SQL-Abfragen mit zitierten Listen (früher ließ sich csv.Sniffer hier auf ',' fehlleiten -> 1 Spalte!)
    sql_csv = (
        header
        + '01.04.2026 09:17:12;updateUser;Update;Admin wurde aktualisiert;admin;105;;;;;'
        + '"INSERT INTO hv_log (a, b, c) VALUES (\'Max\', \'Mustermann\', \'Köln\')"\r\n'
        + '01.04.2026 09:23:54;query;SQL;Abfrage;hv_orders;106;395990;;;;'
        + '"SELECT id, name, email FROM users WHERE city IN (\'Köln\', \'Berlin\', \'München\')"\r\n'
        + '01.04.2026 09:28:31;updateUser;Update;Admin wurde aktualisiert;admin;105;;;;;\r\n'
    )
    loaded_sql = load_csv_bytes(sql_csv.encode("utf-8-sig"))
    assert loaded_sql.delimiter == ";"
    assert len(loaded_sql.headers) == 11
    assert loaded_sql.headers[0] == "Datum"
    assert loaded_sql.headers[-1] == "Neue Daten"
    assert len(loaded_sql.rows) == 3
    assert "INSERT INTO hv_log" in loaded_sql.rows[0]["Neue Daten"]

    # 2. Komma-CSV mit Semikolons innerhalb von Textzellen bleibt korrekt bei ','
    comma_csv = 'Date,Name,Note\n2026-04-01,Max,"a;b;c;d;e"\n2026-04-02,Erika,"x;y;z"\n'
    loaded_comma = load_csv_bytes(comma_csv.encode("utf-8"))
    assert loaded_comma.delimiter == ","
    assert loaded_comma.headers == ["Date", "Name", "Note"]
    assert len(loaded_comma.rows) == 2

    # 3. Tab-getrennte Datei (TSV) bleibt korrekt bei '\t'
    tsv = "Datum\tAktion\tNeue Daten\n2026-04-01\tQuery\t\"SELECT a, b, c FROM t WHERE x IN ('1', '2', '3')\"\n"
    loaded_tsv = load_csv_bytes(tsv.encode("utf-8"))
    assert loaded_tsv.delimiter == "\t"
    assert loaded_tsv.headers == ["Datum", "Aktion", "Neue Daten"]

    # 4. Header mit Komma in einer Semikolon-CSV (z. B. "Name, Vorname") bleibt korrekt bei ';'
    mixed_header_csv = 'Datum;"Name, Vorname";Notiz\n01.04.2026;Mustermann;Test\n02.04.2026;Weber;Ok\n'
    loaded_mixed = load_csv_bytes(mixed_header_csv.encode("utf-8"))
    assert loaded_mixed.delimiter == ";"
    assert loaded_mixed.headers == ["Datum", "Name, Vorname", "Notiz"]
