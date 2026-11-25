"""Offline-Prüfbericht (HTML) & Audit-Log (CSV/JSON) für DSGVO-Dokumentation."""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime

from .models import CATEGORY_LABELS_DE, Category, CellResult, Confidence, RedactionConfig, ScanSummary, SpanMatch


def _highlight_html(text: str, matches: list[SpanMatch]) -> str:
    """Rendert Originaltext mit farbig markierten Fundstellen (Rot=Sicher, Gelb=Prüfen, Lila=Manuell, Grün=Geschützt)."""
    if not matches:
        return html.escape(text).replace("\n", "<br>")

    ordered = sorted(matches, key=lambda m: m.start)
    parts: list[str] = []
    cursor = 0
    for m in ordered:
        if m.start < cursor:
            continue
        parts.append(html.escape(text[cursor:m.start]))
        seg = html.escape(text[m.start:m.end])
        cat_label = CATEGORY_LABELS_DE.get(m.category, m.category.value)
        reasons = html.escape(" | ".join(m.reasons))
        if m.protected:
            css = "badge-protected"
            status = "Antragsteller geschützt"
        elif not m.redacted:
            css = "badge-released"
            status = "Manuell freigegeben"
        elif m.category == Category.MANUAL:
            if "maus" in (m.rule or "").lower():
                css = "badge-manual"
                status = "Manuell geschwärzt (nur hier)"
                cat_label = "Manuell (hier)"
            else:
                css = "badge-manual-global"
                status = "Manuelle Regel überall"
                cat_label = "Manuelle Regel überall"
        elif m.confidence == Confidence.REVIEW:
            css = "badge-review"
            status = "Unsicher (im Zweifel geschwärzt)"
        else:
            css = "badge-high"
            status = "Sicher geschwärzt"
        tooltip = f"{status} · {cat_label} · {reasons}"
        parts.append(f'<mark class="{css}" title="{tooltip}">{seg}<span class="tag">{html.escape(cat_label)}</span></mark>')
        cursor = m.end
    parts.append(html.escape(text[cursor:]))
    return "".join(parts).replace("\n", "<br>")


def build_html_report(
    filename: str,
    summary: ScanSummary,
    cell_results: list[CellResult],
    config: RedactionConfig,
) -> str:
    """Erstellt einen eigenständigen, 100 % offline nutzbaren HTML-Prüfbericht."""
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    applicant_str = (
        f"{config.applicant_first_name} {config.applicant_last_name}".strip()
        or "Keine Ausnahme angegeben"
    )

    cat_pills = "".join(
        f'<span class="pill">{html.escape(k)}: <strong>{v}</strong></span>'
        for k, v in summary.by_category.items()
    ) or '<span class="muted">Keine Schwärzungen</span>'

    review_rows_html: list[str] = []
    all_rows_html: list[str] = []

    is_pdf = filename.lower().endswith(".pdf")
    row_word = "Seite" if is_pdf else "Zeile"
    total_lbl = "PDF-Seiten gesamt" if is_pdf else "CSV-Zeilen gesamt"
    findings_lbl = "Seiten mit Fundstellen" if is_pdf else "Zeilen mit Fundstellen"

    for cell in cell_results:
        has_review = any(not m.protected and m.redacted and m.confidence == Confidence.REVIEW for m in cell.matches)
        orig_hl = _highlight_html(cell.original, cell.matches)
        red_escaped = (
            html.escape(cell.redacted)
            .replace("[GESCHWÄRZT]", "<strong>[GESCHWÄRZT]</strong>")
            .replace("[SECRET]", "<strong>[SECRET]</strong>")
            .replace("\n", "<br>")
        )
        display_num = cell.row_index + 1 if is_pdf else cell.row_index + 2

        row_tr = (
            f'<tr class="{"row-review" if has_review else ""}">'
            f'<td class="col-meta"><strong>{row_word} {display_num}</strong><br>'
            f'<span class="muted">{html.escape(cell.column)}</span></td>'
            f'<td class="col-text">{orig_hl}</td>'
            f'<td class="col-text redacted-col">{red_escaped}</td>'
            f"</tr>"
        )
        all_rows_html.append(row_tr)
        if has_review:
            review_rows_html.append(row_tr)

    review_section = ""
    if review_rows_html:
        review_section = f"""
        <section class="card card-warn">
          <h2>⚠️ Unsichere Fälle zur kurzen Gegenprüfung ({len(review_rows_html)} Zellen)</h2>
          <p class="sub">Diese Stellen wurden nach dem Prinzip <em>„Im Zweifel schwärzen“</em> automatisch geschwärzt. Bitte kurz prüfen, ob etwas Harmloses dabei ist.</p>
          <table>
            <thead><tr><th>Zeile / Spalte</th><th>Original (Fundstellen markiert)</th><th>Geschwärztes Ergebnis</th></tr></thead>
            <tbody>{''.join(review_rows_html)}</tbody>
          </table>
        </section>
        """

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DSGVO-Prüfbericht – {html.escape(filename)}</title>
<style>
  :root {{
    --bg: #f8fafc; --card: #ffffff; --text: #0f172a; --muted: #64748b;
    --border: #e2e8f0; --red-bg: #fee2e2; --red-bd: #dc2626;
    --amber-bg: #fef3c7; --amber-bd: #d97706;
    --green-bg: #ccfbf1; --green-bd: #0d9488;
    --blue-bg: #dbeafe; --blue-bd: #2563eb;
    --slate-bg: #f1f5f9; --slate-bd: #64748b;
    --purple-bg: #f3e8ff; --purple-bd: #9333ea;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 28px; line-height: 1.5; }}
  .wrap {{ max-width: 1440px; margin: 0 auto; }}
  h1 {{ margin: 0 0 6px; font-size: 24px; }}
  h2 {{ margin: 0 0 10px; font-size: 18px; }}
  .sub {{ color: var(--muted); margin: 0 0 16px; font-size: 14px; }}
  .muted {{ color: var(--muted); font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 18px 0; }}
  .stat {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .stat .num {{ font-size: 28px; font-weight: 700; }}
  .stat .lbl {{ color: var(--muted); font-size: 13px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
  .card-warn {{ border-left: 5px solid var(--amber-bd); }}
  .internal-banner {{
    background: #7f1d1d; color: #fff; border-radius: 10px; padding: 12px 16px;
    font-size: 13.5px; font-weight: 600; margin-bottom: 18px;
    border: 1px solid #dc2626;
  }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .pill {{ background: #f1f5f9; border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; font-size: 13px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; margin: 12px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  th, td {{ border: 1px solid var(--border); padding: 10px 12px; vertical-align: top; text-align: left; }}
  th {{ background: #f1f5f9; font-weight: 600; }}
  .col-meta {{ width: 120px; white-space: nowrap; }}
  .col-text {{ width: 44%; word-break: break-word; }}
  .redacted-col {{ background: #fcfcfd; }}
  .row-review {{ background: #fffbeb; }}
  mark {{ padding: 1px 5px; border-radius: 4px; font-weight: 500; }}
  .badge-high {{ background: var(--red-bg); border-bottom: 2px solid var(--red-bd); color: #7f1d1d; }}
  .badge-review {{ background: var(--amber-bg); border-bottom: 2px solid var(--amber-bd); color: #78350f; }}
  .badge-protected {{ background: var(--green-bg); border-bottom: 2px solid var(--green-bd); color: #115e59; font-weight: 600; }}
  .badge-released {{ background: var(--slate-bg); border-bottom: 2px dashed var(--slate-bd); color: #475569; text-decoration: line-through; }}
  .badge-manual {{ background: var(--purple-bg); border-bottom: 2px solid var(--purple-bd); color: #581c87; }}
  .badge-manual-global {{ background: var(--blue-bg); border-bottom: 2px solid var(--blue-bd); color: #1e3a8a; font-weight: 600; }}
  .tag {{ font-size: 10px; font-weight: 700; text-transform: uppercase; margin-left: 4px; opacity: 0.78; }}
  @media print {{ body {{ padding: 0; background: #fff; }} .card {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<div class="wrap">
  <div class="internal-banner">
    ⚠️ VERTRAULICH – NUR FÜR INTERNE PRÜFUNG: Dieser Prüfbericht enthält ungeschwärzte Originaldaten. NIEMALS an den Antragsteller versenden – versende ausschließlich die geschwärzte PDF- oder CSV-Datei!
  </div>
  <h1>DSGVO-Prüfbericht (Art. 15 Auskunft)</h1>
  <p class="sub">
    Datei: <strong>{html.escape(filename)}</strong> · Erstellt am: {timestamp} ·
    Antragsteller (geschützt): <strong>{html.escape(applicant_str)}</strong> ·
    Sprach-KI: <strong>{html.escape(summary.ner_model_name if summary.ner_active else 'deaktiviert (nur Regeln)')}</strong> ·
    Spalten: <strong>{html.escape(', '.join(summary.scanned_columns))}</strong>
  </p>

  <div class="grid">
    <div class="stat"><div class="num">{summary.total_rows}</div><div class="lbl">{total_lbl}</div></div>
    <div class="stat"><div class="num">{summary.rows_with_findings}</div><div class="lbl">{findings_lbl}</div></div>
    <div class="stat"><div class="num" style="color:#dc2626">{summary.total_redactions}</div><div class="lbl">Geschwärzte Stellen</div></div>
    <div class="stat"><div class="num" style="color:#d97706">{summary.review_findings}</div><div class="lbl">Unsichere Fälle (geprüft/markiert)</div></div>
    <div class="stat"><div class="num" style="color:#16a34a">{summary.protected_applicant_hits}</div><div class="lbl">Antragsteller geschützt</div></div>
  </div>

  <section class="card">
    <h2>Fundstellen nach Kategorie</h2>
    <div class="pills">{cat_pills}</div>
    <div class="legend">
      <span><mark class="badge-high">Rot</mark> Sicher geschwärzt</span>
      <span><mark class="badge-review">Gelb</mark> Unsicher (im Zweifel geschwärzt)</span>
      <span><mark class="badge-manual-global">Blau</mark> Manuelle Regel überall</span>
      <span><mark class="badge-manual">Lila</mark> Nur hier geschwärzt</span>
      <span><mark class="badge-protected">👤 Türkis</mark> Antragsteller (bewusst nicht geschwärzt)</span>
      <span><mark class="badge-released">Grau</mark> Manuell freigegeben</span>
    </div>
  </section>

  {review_section}

  <section class="card">
    <h2>Alle Zellen mit Treffern ({len(all_rows_html)})</h2>
    <table>
      <thead><tr><th>Zeile / Spalte</th><th>Original (Fundstellen markiert)</th><th>Geschwärztes Ergebnis</th></tr></thead>
      <tbody>{''.join(all_rows_html) if all_rows_html else '<tr><td colspan="3" class="muted">Keine personenbezogenen Daten gefunden.</td></tr>'}</tbody>
    </table>
  </section>
</div>
</body>
</html>
"""


def build_audit_csv_bytes(cell_results: list[CellResult], include_originals: bool = True) -> bytes:
    """Erstellt ein vollständiges Audit-Protokoll aller Fundstellen als CSV (optional ohne Original-Klartext)."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    header = [
        "CSV_Zeile",
        "Spalte",
        "Status",
        "Kategorie",
        "Konfidenz",
    ]
    if include_originals:
        header.append("Original_Text")
    header.append("Regel_Begruendung")
    writer.writerow(header)

    for cell in cell_results:
        for m in cell.matches:
            if m.protected:
                status = "GESCHUETZT_ANTRAGSTELLER"
            elif m.redacted:
                status = "GESCHWAERZT"
            else:
                status = "MANUELL_FREIGEGEBEN"
            row = [
                cell.row_index + 2,  # 1-basiert inkl. Header
                cell.column,
                status,
                CATEGORY_LABELS_DE.get(m.category, m.category.value),
                m.confidence.value,
            ]
            if include_originals:
                row.append(m.text)
            row.append(" | ".join(m.reasons))
            writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


def build_audit_json(filename: str, summary: ScanSummary, cell_results: list[CellResult]) -> str:
    """Erstellt ein maschinenlesbares JSON-Audit-Protokoll."""
    payload = {
        "filename": filename,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary.to_dict(),
        "cells": [c.to_dict() for c in cell_results],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
