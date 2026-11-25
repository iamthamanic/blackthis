"""Speicherschonende In-Memory-Sitzungsverwaltung mit O(1)-Match-Index und inkrementellen Updates."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .csv_io import LoadedCSV
from .engine import RedactionEngine, apply_redactions_to_text
from .models import (
    CATEGORY_LABELS_DE,
    Category,
    CellResult,
    Confidence,
    RedactionConfig,
    ScanSummary,
    SpanMatch,
)
from .pdf_redactor import PdfDocumentData
from .whitelist import ApplicantWhitelist

PAGINATION_THRESHOLD = 300


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


@dataclass
class ManualSpanRecord:
    """Eine nachträglich per Maus markierte Einzel-Schwärzung in genau einer Zelle."""

    row_index: int
    column: str
    start: int
    end: int
    text: str
    redacted: bool = True
    match_id: str = ""


@dataclass
class ScanSession:
    """In-Memory-Sitzung für eine gescannte Tabelle (100 % lokal im RAM, O(1)-Zugriff auf Fundstellen)."""

    session_id: str
    filename: str
    loaded: LoadedCSV
    config: RedactionConfig
    redacted_rows: list[dict[str, str]]
    cell_results: list[CellResult]
    summary: ScanSummary
    cell_lookup: dict[tuple[int, str], CellResult] = field(default_factory=dict)
    match_lookup: dict[str, tuple[CellResult, SpanMatch]] = field(default_factory=dict)
    manual_spans: list[ManualSpanRecord] = field(default_factory=list)
    toggle_overrides: dict[tuple[int, str, int, int], bool] = field(default_factory=dict)
    ner_by_text: dict[str, list[SpanMatch]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    residual_check: dict[str, Any] = field(default_factory=lambda: {"count": 0, "samples": []})
    pdf_source: PdfDocumentData | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.cell_lookup and self.cell_results:
            self.cell_lookup = {(c.row_index, c.column): c for c in self.cell_results}
        if not self.match_lookup and self.cell_results:
            self.rebuild_match_index()

    def rebuild_match_index(self) -> None:
        """Baut den O(1)-Index match_id -> (CellResult, SpanMatch) auf."""
        idx: dict[str, tuple[CellResult, SpanMatch]] = {}
        for cell in self.cell_results:
            for m in cell.matches:
                if m.match_id:
                    idx[m.match_id] = (cell, m)
        self.match_lookup = idx

    def _index_cell_matches(self, cell: CellResult) -> None:
        for m in cell.matches:
            if m.match_id:
                self.match_lookup[m.match_id] = (cell, m)

    def ensure_cell(self, row_index: int, column: str) -> CellResult:
        """Liefert die CellResult für (row_index, column) oder legt sie für 0-Treffer-Zeilen an."""
        if row_index < 0 or row_index >= len(self.loaded.rows):
            raise ValueError(f"Ungültiger Zeilenindex: {row_index}")
        if column not in self.loaded.headers:
            raise ValueError(f"Unbekannte Spalte: {column}")
        key = (row_index, column)
        existing = self.cell_lookup.get(key)
        if existing is not None:
            return existing
        orig = self.loaded.rows[row_index].get(column, "") or ""
        cell = CellResult(row_index=row_index, column=column, original=orig, redacted=orig, matches=[])
        self.cell_lookup[key] = cell
        self.cell_results.append(cell)
        self.cell_results.sort(key=lambda c: (c.row_index, c.column))
        return cell

    def toggle_match(self, match_id: str, redacted: bool) -> CellResult | None:
        """Schaltet eine Fundstelle in O(1) um und aktualisiert nur die betroffene Zelle."""
        pair = self.match_lookup.get(match_id)
        if pair is None:
            return None
        cell, match = pair
        match.protected = False
        match.redacted = redacted

        for rec in self.manual_spans:
            if rec.match_id == match_id:
                rec.redacted = redacted
                break
        self.toggle_overrides[(cell.row_index, cell.column, match.start, match.end)] = redacted
        self.rebuild_single_cell(cell)
        return cell

    def add_manual_span(
        self,
        row_index: int,
        column: str,
        start: int,
        end: int,
        expected_text: str | None = None,
    ) -> tuple[CellResult, SpanMatch]:
        """Schwärzt einen per Maus markierten Textbereich exakt in dieser Zelle."""
        cell = self.ensure_cell(row_index, column)
        text = cell.original
        if not text:
            raise ValueError("Die ausgewählte Zelle ist leer.")

        s = max(0, min(int(start), len(text)))
        e = max(0, min(int(end), len(text)))

        if expected_text is not None:
            exp_clean = expected_text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if exp_clean and text[s:e].strip() != exp_clean:
                best_pos = -1
                best_dist = 10**9
                search_from = 0
                while True:
                    idx = text.find(exp_clean, search_from)
                    if idx == -1:
                        break
                    dist = abs(idx - s)
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = idx
                    search_from = idx + 1
                if best_pos != -1:
                    s = best_pos
                    e = best_pos + len(exp_clean)

        if s >= e:
            raise ValueError("Bitte einen nicht-leeren Textbereich markieren.")

        selected = text[s:e]
        s += len(selected) - len(selected.lstrip())
        e -= len(selected) - len(selected.rstrip())
        if s >= e:
            raise ValueError("Die Markierung enthält nur Leerzeichen.")

        clean_text = text[s:e]
        match_id = f"manual_{uuid.uuid4().hex[:10]}"
        new_match = SpanMatch(
            start=s,
            end=e,
            text=clean_text,
            category=Category.MANUAL,
            confidence=Confidence.HIGH,
            rule="Manuell markiert (Maus)",
            reasons=["Manuell markiert (Maus)"],
            redacted=True,
            protected=False,
            match_id=match_id,
        )

        removed_ids = [m.match_id for m in cell.matches if _spans_overlap(m.start, m.end, s, e) and m.match_id]
        for rid in removed_ids:
            self.match_lookup.pop(rid, None)

        cell.matches = [m for m in cell.matches if not _spans_overlap(m.start, m.end, s, e)]
        cell.matches.append(new_match)
        cell.matches.sort(key=lambda m: m.start)
        self.match_lookup[match_id] = (cell, new_match)

        self.manual_spans = [
            rec
            for rec in self.manual_spans
            if not (rec.row_index == row_index and rec.column == column and _spans_overlap(rec.start, rec.end, s, e))
        ]
        self.manual_spans.append(ManualSpanRecord(row_index, column, s, e, clean_text, True, match_id))
        self.toggle_overrides[(row_index, column, s, e)] = True

        self.rebuild_single_cell(cell)
        return cell, new_match

    def _apply_cell_manual_edits(self, cell: CellResult, changed_words_l: set[str] | None = None) -> None:
        for rec in self.manual_spans:
            if rec.row_index != cell.row_index or rec.column != cell.column:
                continue
            if rec.start < 0 or rec.end > len(cell.original) or rec.start >= rec.end:
                continue
            span_text = cell.original[rec.start:rec.end]
            cell.matches = [m for m in cell.matches if not _spans_overlap(m.start, m.end, rec.start, rec.end)]
            cell.matches.append(
                SpanMatch(
                    start=rec.start,
                    end=rec.end,
                    text=span_text,
                    category=Category.MANUAL,
                    confidence=Confidence.HIGH,
                    rule="Manuell markiert (Maus)",
                    reasons=["Manuell markiert (Maus)"],
                    redacted=rec.redacted,
                    protected=False,
                    match_id=rec.match_id or f"manual_{uuid.uuid4().hex[:10]}",
                )
            )
            cell.matches.sort(key=lambda m: m.start)

        for m in cell.matches:
            key = (cell.row_index, cell.column, m.start, m.end)
            if key in self.toggle_overrides:
                if changed_words_l and m.text.strip().lower() in changed_words_l:
                    continue
                m.protected = False
                m.redacted = self.toggle_overrides[key]

    def update_replacement(self, replacement: str, use_typed_placeholders: bool = False) -> None:
        """Ändert den Schwärzungs-Text im Nachhinein blitzschnell ohne erneuten Scan."""
        self.config.replacement = replacement or "██████"
        self.config.use_typed_placeholders = bool(use_typed_placeholders)
        for cell in self.cell_results:
            cell.redacted = apply_redactions_to_text(cell.original, cell.matches, self.config)
            self.redacted_rows[cell.row_index][cell.column] = cell.redacted
        self.revision += 1

    def apply_bulk_word_incremental(self, word: str, action: str, engine: RedactionEngine) -> int:
        """Kompatibilitäts-Wrapper für ein einzelnes Wort."""
        return self.apply_bulk_words_incremental([word], action, engine)

    def apply_bulk_words_incremental(self, words: list[str], action: str, engine: RedactionEngine) -> int:
        """Wendet 'Überall schwärzen/freigeben' für ein oder mehrere Wörter in einem schnellen Durchlauf an."""
        words_l = [w.strip().lower() for w in words if w and w.strip()]
        if not words_l:
            return 0
        words_set = set(words_l)
        whitelist = ApplicantWhitelist(
            first_name=self.config.applicant_first_name,
            last_name=self.config.applicant_last_name,
            extra_terms=self.config.extra_whitelist,
        )

        affected_keys: list[tuple[int, str]] = []
        for r_idx, row in enumerate(self.loaded.rows):
            for col in self.summary.scanned_columns:
                val = row.get(col, "") or ""
                if not val:
                    continue
                val_l = val.lower()
                if any(wl in val_l for wl in words_l):
                    affected_keys.append((r_idx, col))

        dirty_order = False
        removed_keys: set[tuple[int, str]] = set()

        for r_idx, col in affected_keys:
            orig = self.loaded.rows[r_idx].get(col, "") or ""
            raw_ner = self.ner_by_text.get(orig)
            ner_list = [
                SpanMatch(m.start, m.end, m.text, m.category, m.confidence, m.rule, list(m.reasons))
                for m in raw_ner
            ] if raw_ner else []

            new_cell = engine.analyze_cell(
                text=orig,
                config=self.config,
                row_index=r_idx,
                column=col,
                ner_matches=ner_list,
                whitelist=whitelist,
            )
            self._apply_cell_manual_edits(new_cell, changed_words_l=words_set)
            new_cell.redacted = apply_redactions_to_text(new_cell.original, new_cell.matches, self.config)
            self.redacted_rows[r_idx][col] = new_cell.redacted

            old_cell = self.cell_lookup.get((r_idx, col))
            if old_cell is not None:
                for om in old_cell.matches:
                    if om.match_id:
                        self.match_lookup.pop(om.match_id, None)

            if new_cell.matches:
                if old_cell is None:
                    self.cell_lookup[(r_idx, col)] = new_cell
                    self.cell_results.append(new_cell)
                    self._index_cell_matches(new_cell)
                    dirty_order = True
                else:
                    old_cell.matches = new_cell.matches
                    old_cell.redacted = new_cell.redacted
                    self._index_cell_matches(old_cell)
            elif old_cell is not None:
                del self.cell_lookup[(r_idx, col)]
                removed_keys.add((r_idx, col))

        if removed_keys:
            self.cell_results = [c for c in self.cell_results if (c.row_index, c.column) not in removed_keys]
        if dirty_order:
            self.cell_results.sort(key=lambda c: (c.row_index, c.column))

        self.recompute_summary()
        self.revision += 1

        return sum(
            1
            for r_idx, col in affected_keys
            if (cell := self.cell_lookup.get((r_idx, col))) is not None
            for m in cell.matches
            if m.text.strip().lower() in words_set
            and ((action == "redact" and m.redacted) or (action == "release" and not m.redacted))
        )

    def recompute_summary(self) -> None:
        """Berechnet die Statistik-Zähler schnell neu."""
        rows_with_findings: set[int] = set()
        cat_counter: Counter[str] = Counter()
        total_redactions = 0
        high_redactions = 0
        review_findings = 0
        protected_hits = 0

        for cell in self.cell_results:
            has_non_protected = False
            for m in cell.matches:
                if m.protected:
                    protected_hits += 1
                else:
                    has_non_protected = True
                    if m.redacted:
                        total_redactions += 1
                        if m.confidence == Confidence.HIGH:
                            high_redactions += 1
                        elif m.confidence == Confidence.REVIEW:
                            review_findings += 1
                        cat_counter[CATEGORY_LABELS_DE.get(m.category, m.category.value)] += 1
            if has_non_protected:
                rows_with_findings.add(cell.row_index)

        self.summary.rows_with_findings = len(rows_with_findings)
        self.summary.total_redactions = total_redactions
        self.summary.high_confidence_redactions = high_redactions
        self.summary.review_findings = review_findings
        self.summary.protected_applicant_hits = protected_hits
        self.summary.by_category = dict(cat_counter.most_common())

    def rebuild_single_cell(self, cell: CellResult) -> None:
        cell.redacted = apply_redactions_to_text(cell.original, cell.matches, self.config)
        self.redacted_rows[cell.row_index][cell.column] = cell.redacted
        self.recompute_summary()
        self.revision += 1

    def get_filtered_cells(
        self,
        filter_mode: str = "all",
        query: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[CellResult], int]:
        """Liefert paginierte Zellen inkl. 0-Treffer-Zeilen (Tab 'Alle Zeilen') ohne unnötige Allokationen."""
        q = query.strip().lower()
        context_cols = [c for c in self.loaded.headers if c not in self.summary.scanned_columns]
        safe_offset = max(0, offset)
        safe_limit = max(1, min(limit, 500))
        end_idx = safe_offset + safe_limit

        def _matches_query(cell: CellResult) -> bool:
            if not q:
                return True
            if q in cell.original.lower() or q in cell.redacted.lower() or q in cell.column.lower() or q == str(cell.row_index + 2):
                return True
            row = self.loaded.rows[cell.row_index]
            return any(q in (row.get(c, "") or "").lower() for c in context_cols)

        # Schneller Pfad für "all" ohne Suchbegriff: direkt per Index berechnen (O(limit) statt O(N)!)
        if filter_mode == "all" and not q:
            cols = self.summary.scanned_columns
            num_cols = len(cols)
            total = len(self.loaded.rows) * num_cols
            page: list[CellResult] = []
            for flat_i in range(safe_offset, min(end_idx, total)):
                r_idx = flat_i // num_cols
                col = cols[flat_i % num_cols]
                existing = self.cell_lookup.get((r_idx, col))
                if existing is not None:
                    page.append(existing)
                else:
                    orig = self.loaded.rows[r_idx].get(col, "") or ""
                    page.append(CellResult(r_idx, col, orig, orig, []))
            return page, total

        page = []
        total = 0
        if filter_mode == "all":
            for r_idx, row in enumerate(self.loaded.rows):
                for col in self.summary.scanned_columns:
                    existing = self.cell_lookup.get((r_idx, col))
                    cell = existing if existing is not None else CellResult(r_idx, col, row.get(col, "") or "", row.get(col, "") or "", [])
                    if _matches_query(cell):
                        if safe_offset <= total < end_idx:
                            page.append(cell)
                        total += 1
        else:
            for cell in self.cell_results:
                if filter_mode == "review" and not any(not m.protected and m.redacted and m.confidence == Confidence.REVIEW for m in cell.matches):
                    continue
                if _matches_query(cell):
                    if safe_offset <= total < end_idx:
                        page.append(cell)
                    total += 1
        return page, total

    def get_table_rows(
        self,
        query: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        """Liefert vollständige CSV-Zeilen über ALLE Spalten (Original + Geschwärzt) für die Tabellen-Ansicht."""
        q = query.strip().lower()
        headers = self.loaded.headers
        scanned_set = set(self.summary.scanned_columns)
        safe_offset = max(0, offset)
        safe_limit = max(1, min(limit, 500))
        end_idx = safe_offset + safe_limit

        def _build_row(r_idx: int) -> dict[str, Any]:
            orig_row = self.loaded.rows[r_idx]
            red_row = self.redacted_rows[r_idx]
            cells_map: dict[str, Any] = {}
            for col in scanned_set:
                cell = self.cell_lookup.get((r_idx, col))
                if cell is not None and cell.matches:
                    cells_map[col] = cell.to_dict()
            return {
                "row_index": r_idx,
                "original": {h: orig_row.get(h, "") or "" for h in headers},
                "redacted": {h: red_row.get(h, "") or "" for h in headers},
                "cells": cells_map,
            }

        # O(limit)-Fast-Path ohne Suchbegriff
        if not q:
            total = len(self.loaded.rows)
            return [_build_row(i) for i in range(safe_offset, min(end_idx, total))], total

        rows_out: list[dict[str, Any]] = []
        total = 0
        for r_idx, orig_row in enumerate(self.loaded.rows):
            red_row = self.redacted_rows[r_idx]
            if (
                q == str(r_idx + 2)
                or any(q in (orig_row.get(h, "") or "").lower() for h in headers)
                or any(q in (red_row.get(h, "") or "").lower() for h in scanned_set)
            ):
                if safe_offset <= total < end_idx:
                    rows_out.append(_build_row(r_idx))
                total += 1
        return rows_out, total


def serialize_session(session: ScanSession) -> dict[str, Any]:
    """Serialisiert eine ScanSession kompakt für das Frontend (paginiert ab 300 Treffer-Zellen)."""
    context_cols = [c for c in session.loaded.headers if c not in session.summary.scanned_columns]
    is_paginated = len(session.cell_results) > PAGINATION_THRESHOLD
    if is_paginated:
        initial_filter = "review" if session.summary.review_findings > 0 else "findings"
        initial_cells, _ = session.get_filtered_cells(filter_mode=initial_filter, offset=0, limit=100)
        row_contexts = {
            str(c.row_index): {col: session.loaded.rows[c.row_index].get(col, "") for col in context_cols}
            for c in initial_cells
        }
    else:
        initial_cells = session.cell_results
        row_contexts = {
            str(idx): {col: row.get(col, "") for col in context_cols}
            for idx, row in enumerate(session.loaded.rows)
        }
    pdf_info = (
        {
            "page_count": session.pdf_source.page_count,
            "pages_with_text": session.pdf_source.pages_with_text,
            "image_only_pages": session.pdf_source.image_only_pages,
        }
        if session.pdf_source is not None
        else None
    )
    return {
        "session_id": session.session_id,
        "filename": session.filename,
        "file_type": "pdf" if session.pdf_source is not None else "table",
        "pdf_info": pdf_info,
        "revision": session.revision,
        "headers": session.loaded.headers,
        "context_columns": context_cols,
        "row_contexts": row_contexts,
        "summary": session.summary.to_dict(),
        "cells": [c.to_dict() for c in initial_cells],
        "findings_count": len(session.cell_results),
        "server_paginated": is_paginated,
        "total_scanned_cells": len(session.loaded.rows) * len(session.summary.scanned_columns),
        "residual_check": session.residual_check,
        "extra_whitelist": session.config.extra_whitelist,
        "extra_blacklist": session.config.extra_blacklist,
    }
