"""100 % lokaler Pure-Python PDF-1.4-Export mit echten schwarzen Schwärzungsbalken (0 Extra-Abhängigkeiten)."""

from __future__ import annotations

import re
import zlib
from typing import Sequence

from .models import RedactionConfig, ScanSummary

# DIN A4 Querformat (Punkte: 1 pt = 1/72 inch)
PAGE_W = 841.89
PAGE_H = 595.28
MARGIN_X = 32.0
MARGIN_TOP = 32.0
MARGIN_BOTTOM = 34.0
USABLE_W = PAGE_W - 2 * MARGIN_X

# Zeichen, die nicht in WinAnsi/CP1252 liegen, sauber auf lesbare Entsprechungen abbilden
_UNICODE_FALLBACKS = str.maketrans({
    "\u2013": "-",   # –
    "\u2014": "-",   # —
    "\u2018": "'",   # ‘
    "\u2019": "'",   # ’
    "\u201a": ",",   # ‚
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\u201e": '"',   # „
    "\u2026": "...", # …
    "\u00a0": " ",   # NBSP
    "\u202f": " ",   # Narrow NBSP
    "\u200b": "",    # Zero-width space
    "\u2192": "->",  # →
    "\u2022": "*",   # •
    "\u2713": "OK",  # ✓
    "\u2714": "OK",  # ✔
    "\u2212": "-",   # −
})

_REDACT_RUN_RE = re.compile(r"(█+|\[GESCHWÄRZT\]|\[SECRET\])")

# Vorkalkulierte ASCII-Breitentabelle (O(1)-Lookup statt Schleifen-Frozenset-Check pro Zeichen)
_NARROW_CHARS = frozenset("ilI1.,:;!'|()[]{}tfj ")
_WIDE_CHARS = frozenset("mwMWÄÖÜ@%#&")
_ASCII_WIDTH_FACTOR: tuple[float, ...] = tuple(
    0.30 if chr(c) in _NARROW_CHARS
    else (0.78 if chr(c) in _WIDE_CHARS else (0.66 if 65 <= c <= 90 else 0.52))
    for c in range(128)
)


def _char_width(ch: str, font_size: float) -> float:
    code = ord(ch)
    if code < 128:
        return font_size * _ASCII_WIDTH_FACTOR[code]
    if ch == "█":
        return font_size * 0.58
    if ch in _WIDE_CHARS:
        return font_size * 0.78
    return font_size * (0.66 if ch.isupper() else 0.52)


def _text_width(text: str, font_size: float) -> float:
    factors = _ASCII_WIDTH_FACTOR
    total = 0.0
    for ch in text:
        code = ord(ch)
        if code < 128:
            total += factors[code]
        elif ch == "█":
            total += 0.58
        elif ch in _WIDE_CHARS:
            total += 0.78
        else:
            total += 0.66 if ch.isupper() else 0.52
    return total * font_size


def _bar_width_for_run(run: str, font_size: float) -> float:
    return min(font_size * 4.6, max(font_size * 1.8, len(run) * font_size * 0.62))


def _line_width(line: str, font_size: float) -> float:
    total = 0.0
    for part in _REDACT_RUN_RE.split(line):
        if not part:
            continue
        if part[0] == "█":
            total += _bar_width_for_run(part, font_size)
        else:
            total += _text_width(part, font_size)
    return total


def _pdf_str(text: str) -> bytes:
    """Kodiert Text nach WinAnsi (CP1252) und escaped PDF-String-Sonderzeichen."""
    cleaned = text.translate(_UNICODE_FALLBACKS)
    raw = cleaned.encode("cp1252", errors="replace")
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # (, ), \
            out.append(0x5C)
            out.append(b)
        elif b < 0x20:
            out.append(0x20)
        else:
            out.append(b)
    out.append(0x29)
    return bytes(out)


def _wrap_cell_text(text: str, max_width: float, font_size: float) -> list[str]:
    """Bricht mehrzeiligen Zellentext sauber auf max_width um (inkl. langer Wörter & Schwärzungsbalken)."""
    if not text:
        return [""]

    safe_max = max(20.0, max_width)
    lines: list[str] = []

    for raw_para in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_para:
            lines.append("")
            continue
        words = raw_para.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _line_width(candidate, font_size) <= safe_max:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # Einzelnes Wort ist länger als die Spaltenbreite -> hart umbrechen
            if _line_width(word, font_size) <= safe_max:
                current = word
            else:
                buf = ""
                for ch in word:
                    if buf and _line_width(buf + ch, font_size) > safe_max:
                        lines.append(buf)
                        buf = ch
                    else:
                        buf += ch
                current = buf
        if current or not words:
            lines.append(current)

    return lines or [""]


def _compute_column_widths(headers: Sequence[str], rows: Sequence[dict[str, str]]) -> list[float]:
    """Berechnet harmonische Spaltenbreiten (Textspalten bekommen deutlich mehr Platz)."""
    if not headers:
        return []
    text_names = {"notiz", "notizen", "nachricht", "nachrichten", "text", "kommentar", "bemerkung", "beschreibung", "inhalt"}
    sample = rows[:120]
    weights: list[float] = []

    for h in headers:
        hl = h.strip().lower()
        if hl in text_names:
            weights.append(5.8)
            continue
        avg_len = 0.0
        if sample:
            avg_len = sum(len(str(r.get(h, "") or "")) for r in sample) / len(sample)
        if avg_len > 65:
            weights.append(5.2)
        elif avg_len > 25:
            weights.append(2.2)
        else:
            weights.append(max(1.0, min(1.8, max(len(h), avg_len) / 10.0)))

    total_w = sum(weights) or 1.0
    widths = [(w / total_w) * USABLE_W for w in weights]
    # Mindestbreite pro Spalte absichern
    min_col = min(56.0, USABLE_W / len(headers))
    widths = [max(min_col, w) for w in widths]
    scale = USABLE_W / sum(widths)
    return [w * scale for w in widths]


def _draw_line_with_redaction_bars(
    ops: list[bytes],
    line: str,
    x_start: float,
    baseline_y: float,
    font_size: float,
    bold: bool = False,
) -> None:
    """Zeichnet eine Textzeile und ersetzt █-Läufe durch echte gefüllte schwarze Vektorbalken."""
    font_tag = b"/F2" if bold else b"/F1"
    x = x_start
    for part in _REDACT_RUN_RE.split(line):
        if not part:
            continue
        if part[0] == "█":
            bar_w = _bar_width_for_run(part, font_size)
            bar_h = font_size + 1.4
            bar_y = baseline_y - 1.8
            ops.append(
                f"0.04 0.06 0.11 rg {x:.2f} {bar_y:.2f} {bar_w:.2f} {bar_h:.2f} re f\n".encode("ascii")
            )
            x += bar_w + 0.8
        elif part in ("[GESCHWÄRZT]", "[SECRET]"):
            ops.append(
                b"0.02 0.04 0.09 rg BT /F2"
                + f" {font_size:.2f} Tf {x:.2f} {baseline_y:.2f} Td ".encode("ascii")
                + _pdf_str(part)
                + b" Tj ET\n"
            )
            x += _text_width(part, font_size) * 1.04
        else:
            ops.append(
                b"0.08 0.11 0.18 rg BT "
                + font_tag
                + f" {font_size:.2f} Tf {x:.2f} {baseline_y:.2f} Td ".encode("ascii")
                + _pdf_str(part)
                + b" Tj ET\n"
            )
            x += _text_width(part, font_size)


def dump_pdf_bytes(
    headers: list[str],
    rows: list[dict[str, str]],
    filename: str = "auskunft.csv",
    summary: ScanSummary | None = None,
    config: RedactionConfig | None = None,
) -> bytes:
    """Erstellt eine druckfertige, mehrseitige DIN-A4-Querformat-PDF mit echten schwarzen Schwärzungsbalken."""
    col_widths = _compute_column_widths(headers, rows)
    font_size = 8.4 if len(headers) <= 7 else 7.4
    header_font_size = 8.6 if len(headers) <= 7 else 7.6
    line_height = font_size + 3.0
    cell_pad_x = 5.0
    cell_pad_y = 4.5

    pages_ops: list[list[bytes]] = []

    def _start_page() -> tuple[list[bytes], float]:
        ops: list[bytes] = []
        y = PAGE_H - MARGIN_TOP

        # Tabellen-Kopfzeile (wird auf jeder Seite wiederholt)
        th_h = 20.0
        th_y = y - th_h
        ops.append(
            f"0.12 0.16 0.23 rg {MARGIN_X:.2f} {th_y:.2f} {USABLE_W:.2f} {th_h:.2f} re f\n".encode("ascii")
        )
        cx = MARGIN_X
        for h, cw in zip(headers, col_widths):
            short_h = _wrap_cell_text(h, cw - 2 * cell_pad_x, header_font_size)[0]
            ops.append(
                b"1 1 1 rg BT /F2 "
                + f"{header_font_size:.2f} Tf {cx + cell_pad_x:.2f} {th_y + 6.5:.2f} Td ".encode("ascii")
                + _pdf_str(short_h)
                + b" Tj ET\n"
            )
            cx += cw

        pages_ops.append(ops)
        return ops, th_y

    cur_ops, cur_y = _start_page()
    min_y = MARGIN_BOTTOM + 14.0

    for row_idx, row in enumerate(rows):
        wrapped_cols: list[list[str]] = []
        max_lines = 1
        for h, cw in zip(headers, col_widths):
            val = str(row.get(h, "") or "")
            w_lines = _wrap_cell_text(val, cw - 2 * cell_pad_x, font_size)
            wrapped_cols.append(w_lines)
            if len(w_lines) > max_lines:
                max_lines = len(w_lines)

        # Sehr lange Einzelzellen bei Bedarf sauber über Seiten verteilen
        line_offset = 0
        while line_offset < max_lines:
            available_h = cur_y - min_y
            max_fit = int((available_h - 2 * cell_pad_y) // line_height)
            if max_fit < 1:
                cur_ops, cur_y = _start_page()
                available_h = cur_y - min_y
                max_fit = max(1, int((available_h - 2 * cell_pad_y) // line_height))

            chunk_lines = min(max_lines - line_offset, max_fit)
            row_h = chunk_lines * line_height + 2 * cell_pad_y
            row_bottom = cur_y - row_h

            # Dezente Zebra-Schattierung
            if row_idx % 2 == 1:
                cur_ops.append(
                    f"0.97 0.98 0.99 rg {MARGIN_X:.2f} {row_bottom:.2f} {USABLE_W:.2f} {row_h:.2f} re f\n".encode("ascii")
                )

            # Rahmenlinie
            cur_ops.append(
                f"0.86 0.89 0.93 RG 0.4 w {MARGIN_X:.2f} {row_bottom:.2f} {USABLE_W:.2f} {row_h:.2f} re S\n".encode("ascii")
            )

            # Spalten-Trennlinien + Zellentext mit echten schwarzen Schwärzungsbalken
            cx = MARGIN_X
            for col_i, (col_lines, cw) in enumerate(zip(wrapped_cols, col_widths)):
                if col_i > 0:
                    cur_ops.append(
                        f"0.88 0.90 0.94 RG 0.35 w {cx:.2f} {row_bottom:.2f} m {cx:.2f} {cur_y:.2f} l S\n".encode("ascii")
                    )
                slice_lines = col_lines[line_offset:line_offset + chunk_lines]
                for li, line_txt in enumerate(slice_lines):
                    if not line_txt:
                        continue
                    baseline_y = cur_y - cell_pad_y - font_size - (li * line_height)
                    _draw_line_with_redaction_bars(
                        cur_ops,
                        line_txt,
                        x_start=cx + cell_pad_x,
                        baseline_y=baseline_y,
                        font_size=font_size,
                    )
                cx += cw

            cur_y = row_bottom
            line_offset += chunk_lines

    # Seitenzahlen in Fußzeile auf jeder Seite ergänzen
    total_pages = len(pages_ops)
    for page_num, ops in enumerate(pages_ops, start=1):
        footer_right = f"Seite {page_num} / {total_pages}"
        right_w = _text_width(footer_right, 7.8)
        ops.append(
            b"0.45 0.51 0.60 rg BT /F1 7.8 Tf "
            + f"{PAGE_W - MARGIN_X - right_w:.2f} {MARGIN_BOTTOM - 14:.2f} Td ".encode("ascii")
            + _pdf_str(footer_right)
            + b" Tj ET\n"
        )

    # PDF-1.4-Binärstruktur zusammensetzen
    objects: list[bytes] = []
    # 1: Catalog, 2: Pages, 3: Helvetica, 4: Helvetica-Bold, 5: Info
    pdf_title = "Export"
    page_obj_ids: list[int] = []
    next_id = 6
    page_pairs: list[tuple[int, int, bytes]] = []
    for ops in pages_ops:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        compressed = zlib.compress(b"".join(ops), level=6)
        page_pairs.append((content_id, page_id, compressed))
        page_obj_ids.append(page_id)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Title " + _pdf_str(pdf_title) + b" >>")

    for content_id, page_id, compressed in page_pairs:
        stream_obj = (
            f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
            + compressed
            + b"\nendstream"
        )
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W:.2f} {PAGE_H:.2f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects.append(stream_obj)
        objects.append(page_obj)

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj_num, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)
