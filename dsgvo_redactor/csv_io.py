"""Robuste CSV- und Excel-Ein-/Ausgabe mit automatischer Encoding- und Trennzeichen-Erkennung."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoadedCSV:
    """Eingelesene CSV-Daten inkl. erkannter Format-Metadaten."""

    headers: list[str]
    rows: list[dict[str, str]]
    encoding: str
    delimiter: str


CANDIDATE_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1")
CANDIDATE_DELIMITERS = (";", ",", "\t", "|")


def decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    """Erkennt das Text-Encoding zuverlässig (inkl. deutscher Umlaute & Excel UTF-8-BOM / CP1252)."""
    for enc in CANDIDATE_ENCODINGS:
        try:
            text = raw.decode(enc)
            return text, enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def detect_delimiter(sample_text: str) -> str:
    """Erkennt das CSV-Trennzeichen zuverlässig – auch bei mehrzeiligen Zellen in Anführungszeichen."""
    # Erste Zeilen für Sniffer nehmen
    sample = sample_text[:16384]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS))
        if dialect.delimiter in CANDIDATE_DELIMITERS:
            return dialect.delimiter
    except csv.Error:
        pass

    # Fallback: Header-Zeile analysieren
    first_line = sample_text.splitlines()[0] if sample_text.splitlines() else ""
    best_delim = ";"
    best_count = -1
    for delim in CANDIDATE_DELIMITERS:
        cnt = first_line.count(delim)
        if cnt > best_count:
            best_count = cnt
            best_delim = delim
    return best_delim


def load_csv_bytes(raw: bytes, delimiter: str | None = None) -> LoadedCSV:
    """Liest eine CSV aus Bytes inkl. mehrzeiliger Zellen verlustfrei ein."""
    text, encoding = decode_csv_bytes(raw)
    delim = delimiter or detect_delimiter(text)

    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, delimiter=delim)
    all_rows = list(reader)
    if not all_rows:
        return LoadedCSV(headers=[], rows=[], encoding=encoding, delimiter=delim)

    raw_headers = all_rows[0]
    # Doppelte oder leere Header absichern
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx, h in enumerate(raw_headers):
        name = h.strip() or f"Spalte_{idx + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        headers.append(name)

    dict_rows: list[dict[str, str]] = []
    for raw_row in all_rows[1:]:
        # Komplett leere Zeilen am Dateiende ignorieren
        if not raw_row or all(not cell.strip() for cell in raw_row):
            continue
        row_dict: dict[str, str] = {}
        for idx, h in enumerate(headers):
            val = raw_row[idx] if idx < len(raw_row) else ""
            # Zeilenumbrüche auf \n vereinheitlichen, damit Python- und Browser-Offsets 1:1 übereinstimmen
            row_dict[h] = val.replace("\r\n", "\n").replace("\r", "\n")
        dict_rows.append(row_dict)

    return LoadedCSV(headers=headers, rows=dict_rows, encoding=encoding, delimiter=delim)


def load_csv_file(path: str | Path, delimiter: str | None = None) -> LoadedCSV:
    """Liest eine lokale CSV- oder Excel-Datei (.xlsx) ein."""
    p = Path(path)
    raw = p.read_bytes()
    return load_tabular_bytes(raw, filename=p.name, delimiter=delimiter)


def load_xlsx_bytes(raw: bytes) -> LoadedCSV:
    """Liest das erste Tabellenblatt einer Excel-Datei (.xlsx) verlustfrei als LoadedCSV ein."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    raw_rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not raw_rows:
        return LoadedCSV(headers=[], rows=[], encoding="xlsx", delimiter=";")

    raw_headers = ["" if v is None else str(v) for v in raw_rows[0]]
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx, h in enumerate(raw_headers):
        name = h.strip() or f"Spalte_{idx + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        headers.append(name)

    dict_rows: list[dict[str, str]] = []
    for raw_row in raw_rows[1:]:
        if not raw_row or all(v is None or not str(v).strip() for v in raw_row):
            continue
        row_dict: dict[str, str] = {}
        for idx, h in enumerate(headers):
            cell_v = raw_row[idx] if idx < len(raw_row) else None
            val = "" if cell_v is None else str(cell_v)
            row_dict[h] = val.replace("\r\n", "\n").replace("\r", "\n")
        dict_rows.append(row_dict)

    return LoadedCSV(headers=headers, rows=dict_rows, encoding="xlsx", delimiter=";")


def load_tabular_bytes(raw: bytes, filename: str = "", delimiter: str | None = None) -> LoadedCSV:
    """Erkennt automatisch Excel (.xlsx) oder CSV und liest die Tabelle ein."""
    is_xlsx = filename.lower().endswith(".xlsx") or raw[:4] == b"PK\x03\x04"
    if is_xlsx:
        return load_xlsx_bytes(raw)
    return load_csv_bytes(raw, delimiter=delimiter)


def dump_csv_bytes(
    headers: list[str],
    rows: list[dict[str, str]],
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
) -> bytes:
    """Schreibt CSV-Daten als Bytes (Standard: UTF-8 mit BOM für fehlerfreie Umlaute in Excel auf Mac)."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(
        buf,
        fieldnames=headers,
        delimiter=delimiter,
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in headers})
    return buf.getvalue().encode(encoding, errors="replace")


def write_csv_file(
    path: str | Path,
    headers: list[str],
    rows: list[dict[str, str]],
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
) -> None:
    """Speichert geschwärzte CSV-Daten auf Festplatte."""
    data = dump_csv_bytes(headers, rows, delimiter=delimiter, encoding=encoding)
    Path(path).write_bytes(data)


def dump_xlsx_bytes(headers: list[str], rows: list[dict[str, str]]) -> bytes:
    """Exportiert die geschwärzte Tabelle als sauber formatierte Excel-Datei (.xlsx)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Geschwärzte Auskunft"

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")

    wrap_align = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
        cur_row = ws.max_row
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=cur_row, column=col_idx).alignment = wrap_align

    # Sinnvolle Spaltenbreiten setzen
    for col_idx, h in enumerate(headers, start=1):
        col_letter = ws.cell(row=1, column=col_idx).column_letter
        if h.strip().lower() in {"notiz", "notizen", "nachricht", "text", "kommentar", "beschreibung"}:
            ws.column_dimensions[col_letter].width = 85
        else:
            max_len = max([len(h)] + [min(35, len(str(r.get(h, "")))) for r in rows[:200]])
            ws.column_dimensions[col_letter].width = max(14, max_len + 4)

    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
