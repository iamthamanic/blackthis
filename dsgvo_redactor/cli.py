"""Kommandozeilen-Interface (CLI) für den DSGVO CSV Redactor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .csv_io import dump_xlsx_bytes, load_csv_file, write_csv_file
from .engine import RedactionEngine
from .models import RedactionConfig
from .pdf_redactor import apply_pdf_redactions_to_bytes, extract_pdf_document, is_pdf_bytes
from .report import build_audit_csv_bytes, build_audit_json, build_html_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="redact_csv.py",
        description="DSGVO CSV Redactor – 100 % lokales Schwärzungstool für DSGVO-Auskünfte (ohne Cloud / ohne LLM).",
    )
    p.add_argument("-i", "--input", required=True, help="Pfad zur Eingabe-CSV-Datei.")
    p.add_argument(
        "-o",
        "--output",
        help="Pfad zur geschwärzten Ausgabe-CSV (Standard: <eingabe>_geschwaerzt.csv).",
    )
    p.add_argument("--vorname", default="", help="Vorname des Antragstellers (bleibt ungeschwärzt).")
    p.add_argument("--nachname", default="", help="Nachname des Antragstellers (bleibt ungeschwärzt).")
    p.add_argument(
        "--whitelist",
        nargs="*",
        default=[],
        help="Weitere Begriffe, die NICHT geschwärzt werden sollen (z. B. Kürzel, Geburtsname).",
    )
    p.add_argument(
        "--blacklist",
        nargs="*",
        default=[],
        help="Weitere Begriffe, die IMMER geschwärzt werden sollen.",
    )
    p.add_argument(
        "--columns",
        nargs="*",
        default=None,
        help="Spaltennamen, die gescannt werden sollen (Standard: automatisch z. B. 'Notiz').",
    )
    p.add_argument(
        "--replacement",
        default="██████",
        help="Ersetzungstext für geschwärzte Stellen (Standard: ██████).",
    )
    p.add_argument(
        "--typed-placeholders",
        action="store_true",
        help="Verwendet kategoriebezogene Platzhalter wie [NAME], [IBAN] statt einheitlichem Balken.",
    )
    p.add_argument(
        "--redact-companies",
        action="store_true",
        help="Schwärzt zusätzlich Firmennamen / Organisationen (standardmäßig deaktiviert).",
    )
    p.add_argument(
        "--no-locations",
        action="store_true",
        help="Deaktiviert die Schwärzung alleinstehender Ortschaften / Städte (Behörden wie 'Stadt Köln' bleiben immer sichtbar).",
    )
    p.add_argument(
        "--keep-uncertain",
        action="store_true",
        help="Unsichere Fälle NICHT automatisch schwärzen (Standard: im Zweifel schwärzen).",
    )
    p.add_argument(
        "--no-ner",
        action="store_true",
        help="Deaktiviert die lokale spaCy-Sprach-KI (nur feste Regeln & Listen).",
    )
    p.add_argument(
        "--delimiter",
        default=None,
        help="Optional festes CSV-Trennzeichen (Standard: automatische Erkennung).",
    )
    p.add_argument(
        "--report",
        default=None,
        help="Pfad für HTML-Prüfbericht (Standard: <output>_pruefbericht.html). 'none' zum Deaktivieren.",
    )
    p.add_argument(
        "--audit-csv",
        default=None,
        help="Optionaler Pfad für detailliertes Audit-Protokoll als CSV.",
    )
    p.add_argument(
        "--audit-json",
        default=None,
        help="Optionaler Pfad für detailliertes Audit-Protokoll als JSON.",
    )
    p.add_argument(
        "--xlsx",
        default=None,
        help="Optionaler Pfad für zusätzlichen Excel-Export (.xlsx).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Fehler: Datei nicht gefunden: {input_path}", file=sys.stderr)
        return 1

    raw_input = input_path.read_bytes()
    pdf_doc = None
    if is_pdf_bytes(raw_input, input_path.name):
        pdf_doc = extract_pdf_document(raw_input)
        loaded = pdf_doc.loaded
        output_path = (
            Path(args.output)
            if args.output
            else input_path.with_name(f"{input_path.stem}_geschwaerzt.pdf")
        )
    else:
        loaded = load_csv_file(input_path, delimiter=args.delimiter)
        output_path = (
            Path(args.output)
            if args.output
            else input_path.with_name(f"{input_path.stem}_geschwaerzt.csv")
        )
    if not loaded.headers:
        print("Fehler: Eingabedatei scheint leer zu sein.", file=sys.stderr)
        return 1

    config = RedactionConfig(
        applicant_first_name=args.vorname,
        applicant_last_name=args.nachname,
        extra_whitelist=args.whitelist,
        extra_blacklist=args.blacklist,
        replacement=args.replacement,
        use_typed_placeholders=args.typed_placeholders,
        redact_companies=args.redact_companies,
        redact_locations=not args.no_locations,
        redact_uncertain_by_default=not args.keep_uncertain,
        use_ner=not args.no_ner,
        columns=["Text"] if pdf_doc is not None else args.columns,
    )

    engine = RedactionEngine()
    redacted_rows, cell_results, summary = engine.scan_rows(
        headers=loaded.headers,
        rows=loaded.rows,
        config=config,
        encoding=loaded.encoding,
        delimiter=loaded.delimiter,
    )

    if pdf_doc is not None:
        output_path.write_bytes(apply_pdf_redactions_to_bytes(pdf_doc, cell_results, config))
    else:
        write_csv_file(
            output_path,
            headers=loaded.headers,
            rows=redacted_rows,
            delimiter=loaded.delimiter,
            encoding="utf-8-sig",
        )

    report_path: Path | None = None
    if args.report != "none":
        report_path = (
            Path(args.report)
            if args.report
            else output_path.with_name(f"{output_path.stem}_pruefbericht.html")
        )
        html_content = build_html_report(input_path.name, summary, cell_results, config)
        report_path.write_text(html_content, encoding="utf-8")

    if args.audit_csv:
        Path(args.audit_csv).write_bytes(build_audit_csv_bytes(cell_results))

    if args.audit_json:
        Path(args.audit_json).write_text(
            build_audit_json(input_path.name, summary, cell_results), encoding="utf-8"
        )

    if args.xlsx:
        Path(args.xlsx).write_bytes(dump_xlsx_bytes(loaded.headers, redacted_rows))

    unit_lbl = "Seiten" if pdf_doc is not None else "Zeilen"
    out_lbl = "Ausgabe-PDF:" if pdf_doc is not None else "Ausgabe-CSV:"
    print("=== BLACKTHIS – DSGVO Redactor Ergebnis ===")
    print(f"Eingabe:               {input_path} ({summary.total_rows} {unit_lbl}, Encoding {loaded.encoding})")
    print(f"Gescannte Spalten:     {', '.join(summary.scanned_columns)}")
    print(f"Lokale Sprach-KI:      {summary.ner_model_name if summary.ner_active else 'DEAKTIVIERT (nur Regeln)'}")
    print(f"Antragsteller:         {(args.vorname + ' ' + args.nachname).strip() or '—'} ({summary.protected_applicant_hits} geschützte Stellen)")
    print(f"Geschwärzte Stellen:   {summary.total_redactions} (davon {summary.review_findings} unsichere Fälle zur Prüfung)")
    if summary.by_category:
        print("Nach Kategorie:")
        for cat, cnt in summary.by_category.items():
            print(f"  - {cat}: {cnt}")
    print(f"{out_lbl:<23}{output_path}")
    if report_path:
        print(f"HTML-Prüfbericht:      {report_path}")
    if args.xlsx:
        print(f"Excel-Export:          {args.xlsx}")
    if args.audit_csv:
        print(f"Audit-CSV:             {args.audit_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
