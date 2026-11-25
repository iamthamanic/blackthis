"""Zentrale Schwärzungs-Engine: kombiniert Regeln, lokales Offline-NER, Memoization & Antragsteller-Schutz."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from .detectors import run_rule_detectors
from .detectors_common import LEGAL_FORM_TAIL_RE
from .detectors_entities import matches_job_stem, normalize_job_token
from .lexicons import COMMON_FIRST_NAMES, COMMON_SURNAMES, MAJOR_GERMAN_CITIES, NON_NAME_WORDS, ORG_NOISE_WORDS
from .models import (
    CATEGORY_LABELS_DE,
    CATEGORY_PRIORITY,
    Category,
    CellResult,
    Confidence,
    RedactionConfig,
    ScanSummary,
    SpanMatch,
)
from .ner_engine import LocalGermanNER
from .whitelist import ApplicantWhitelist, ProtectedSpan

_JSON_KEY_TOKEN_RE = re.compile(r'(?<=[{,])\s*"([A-Za-z0-9_\-äöüÄÖÜß]{1,50})"\s*:')


def _find_json_key_spans(text: str) -> list[tuple[int, int]]:
    """Liefert Zeichenbereiche von JSON-Feldnamen (z. B. "berater":null), damit Schlüssel nicht geschwärzt werden."""
    if '":' not in text and '" :' not in text:
        return []
    return [(m.start(1), m.end(1)) for m in _JSON_KEY_TOKEN_RE.finditer(text)]


def _clone_match(m: SpanMatch, match_id: str = "") -> SpanMatch:
    return SpanMatch(
        start=m.start,
        end=m.end,
        text=m.text,
        category=m.category,
        confidence=m.confidence,
        rule=m.rule,
        reasons=list(m.reasons),
        redacted=m.redacted,
        protected=m.protected,
        match_id=match_id,
    )


def apply_redactions_to_text(
    text: str,
    matches: list[SpanMatch],
    config: RedactionConfig,
) -> str:
    """Wendet alle als `redacted=True` markierten Spans sauber von hinten nach vorne an."""
    if not text or not matches:
        return text

    active = [m for m in matches if m.redacted and not m.protected]
    if not active:
        return text

    active.sort(key=lambda m: (m.start, -m.end))
    parts: list[str] = []
    cursor = 0
    for m in active:
        if m.start < cursor:
            continue
        parts.append(text[cursor:m.start])
        parts.append(config.replacement_for(m.category))
        cursor = m.end
    parts.append(text[cursor:])
    return "".join(parts)


class RedactionEngine:
    """Orchestriert alle Erkennungsschichten, Konfliktauflösung, Memoization und Antragsteller-Whitelist."""

    def __init__(self, ner: LocalGermanNER | None = None) -> None:
        self.ner = ner if ner is not None else LocalGermanNER()

    @staticmethod
    def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        return a_start < b_end and b_start < a_end

    def _resolve_overlaps(self, text: str, raw_matches: list[SpanMatch]) -> list[SpanMatch]:
        """Löst überlappende Treffer deterministisch auf und bündelt übereinstimmende Gründe."""
        if not raw_matches:
            return []

        text_len = len(text)
        grouped: dict[tuple[int, int], list[SpanMatch]] = {}
        for m in raw_matches:
            if 0 <= m.start < m.end <= text_len:
                grouped.setdefault((m.start, m.end), []).append(m)

        deduped: list[SpanMatch] = []
        for (start, end), group in grouped.items():
            if len(group) == 1:
                m0 = group[0]
                deduped.append(SpanMatch(start, end, text[start:end], m0.category, m0.confidence, m0.rule, list(m0.reasons)))
                continue

            group.sort(
                key=lambda x: (1 if x.confidence == Confidence.HIGH else 0, CATEGORY_PRIORITY.get(x.category, 0)),
                reverse=True,
            )
            best = group[0]
            all_reasons = list(dict.fromkeys(r for item in group for r in item.reasons))
            conf = Confidence.HIGH if (any(x.confidence == Confidence.HIGH for x in group) or len(all_reasons) >= 2) else Confidence.REVIEW
            deduped.append(SpanMatch(start, end, text[start:end], best.category, conf, best.rule, all_reasons))

        deduped.sort(
            key=lambda m: (CATEGORY_PRIORITY.get(m.category, 0), 1 if m.confidence == Confidence.HIGH else 0, m.end - m.start, -m.start),
            reverse=True,
        )

        accepted: list[SpanMatch] = []
        for cand in deduped:
            overlapping = [a for a in accepted if self._spans_overlap(cand.start, cand.end, a.start, a.end)]
            if not overlapping:
                accepted.append(cand)
                continue

            for acc in overlapping:
                if acc.start <= cand.start and cand.end <= acc.end:
                    for r in cand.reasons:
                        if r not in acc.reasons:
                            acc.reasons.append(r)
                    if cand.confidence == Confidence.HIGH:
                        acc.confidence = Confidence.HIGH
                elif (
                    acc.category == Category.NAME
                    and cand.category == Category.NAME
                    and (cand.end - cand.start) > (acc.end - acc.start)
                ):
                    acc.start = min(acc.start, cand.start)
                    acc.end = max(acc.end, cand.end)
                    acc.text = text[acc.start:acc.end]
                    for r in cand.reasons:
                        if r not in acc.reasons:
                            acc.reasons.append(r)
                    if cand.confidence == Confidence.HIGH:
                        acc.confidence = Confidence.HIGH

        accepted.sort(key=lambda m: m.start)

        merged: list[SpanMatch] = []
        for m in accepted:
            if (
                merged
                and merged[-1].category == Category.NAME
                and m.category == Category.NAME
                and text[merged[-1].end:m.start] == " "
            ):
                prev = merged[-1]
                prev.end = m.end
                prev.text = text[prev.start:prev.end]
                for r in m.reasons:
                    if r not in prev.reasons:
                        prev.reasons.append(r)
                if m.confidence == Confidence.HIGH:
                    prev.confidence = Confidence.HIGH
            else:
                merged.append(m)

        return merged

    @staticmethod
    def _is_meaningful_remainder(piece: str) -> bool:
        """Prüft, ob nach dem Herausschneiden eines geschützten Antragsteller-Spans noch ein echter Name übrig bleibt."""
        clean = piece.strip(" \t\r\n.,:;!?()[]{}\"'<>/\\|-")
        if len(clean) < 2 or "<" in clean or ">" in clean or "=" in clean:
            return False
        if not any(ch.isalpha() for ch in clean):
            return False
        tokens = [t.strip(".,:;!?()[]{}\"'").lower() for t in clean.split() if t.strip()]
        if not tokens:
            return False
        return not all(
            t in NON_NAME_WORDS or t in {"herr", "herrn", "frau", "hr", "fr", "dr", "prof"}
            for t in tokens
        )

    def _apply_whitelist(
        self,
        matches: list[SpanMatch],
        protected_spans: list[ProtectedSpan],
        whitelist: ApplicantWhitelist,
    ) -> list[SpanMatch]:
        if not protected_spans:
            return matches

        result: list[SpanMatch] = []
        covered_protected_idx: set[int] = set()
        emitted_protected_idx: set[int] = set()

        for m in matches:
            matched_ps_idx: int | None = None
            overlapping_eligible: list[tuple[int, ProtectedSpan]] = []
            for idx, ps in enumerate(protected_spans):
                if m.category == Category.NAME:
                    if (ps.start <= m.start and m.end <= ps.end) or (
                        self._spans_overlap(m.start, m.end, ps.start, ps.end) and whitelist.is_name_of_applicant(m.text)
                    ):
                        matched_ps_idx = idx
                        break
                    if self._spans_overlap(m.start, m.end, ps.start, ps.end):
                        overlapping_eligible.append((idx, ps))
                elif ps.reason.startswith("Ausnahme-Liste"):
                    if ps.start <= m.start and m.end <= ps.end:
                        matched_ps_idx = idx
                        break
                    if self._spans_overlap(m.start, m.end, ps.start, ps.end):
                        overlapping_eligible.append((idx, ps))

            if matched_ps_idx is not None:
                ps = protected_spans[matched_ps_idx]
                covered_protected_idx.add(matched_ps_idx)
                m.protected = True
                m.redacted = False
                m.reasons.append(f"Geschützt: {ps.reason}")
                result.append(m)
                continue

            if overlapping_eligible:
                # Größerer Treffer (z. B. durch HTML-Tag oder Nachbarwort) enthält geschützten Antragsteller:
                # Antragsteller sauber herausschneiden & schützen, nur echte Rest-Namen behalten.
                overlapping_eligible.sort(key=lambda item: item[1].start)
                cursor = m.start
                for idx, ps in overlapping_eligible:
                    if ps.start > cursor:
                        raw_rem = m.text[cursor - m.start : ps.start - m.start]
                        if self._is_meaningful_remainder(raw_rem):
                            lead = len(raw_rem) - len(raw_rem.lstrip(" \t\r\n.,:;!?()[]{}\"'<>/\\|-"))
                            trail = len(raw_rem) - len(raw_rem.rstrip(" \t\r\n.,:;!?()[]{}\"'<>/\\|-"))
                            rs = cursor + lead
                            re_ = ps.start - trail
                            if rs < re_:
                                result.append(
                                    SpanMatch(
                                        start=rs,
                                        end=re_,
                                        text=m.text[rs - m.start : re_ - m.start],
                                        category=m.category,
                                        confidence=m.confidence,
                                        rule=m.rule,
                                        reasons=list(m.reasons),
                                        redacted=m.redacted,
                                        protected=False,
                                    )
                                )
                    covered_protected_idx.add(idx)
                    if idx not in emitted_protected_idx:
                        emitted_protected_idx.add(idx)
                        result.append(
                            SpanMatch(
                                start=ps.start,
                                end=ps.end,
                                text=ps.text,
                                category=Category.NAME,
                                confidence=Confidence.HIGH,
                                rule=ps.reason,
                                reasons=[f"Geschützt: {ps.reason}"],
                                redacted=False,
                                protected=True,
                            )
                        )
                    cursor = max(cursor, ps.end)

                if cursor < m.end:
                    raw_rem = m.text[cursor - m.start :]
                    if self._is_meaningful_remainder(raw_rem):
                        lead = len(raw_rem) - len(raw_rem.lstrip(" \t\r\n.,:;!?()[]{}\"'<>/\\|-"))
                        trail = len(raw_rem) - len(raw_rem.rstrip(" \t\r\n.,:;!?()[]{}\"'<>/\\|-"))
                        rs = cursor + lead
                        re_ = m.end - trail
                        if rs < re_:
                            result.append(
                                SpanMatch(
                                    start=rs,
                                    end=re_,
                                    text=m.text[rs - m.start : re_ - m.start],
                                    category=m.category,
                                    confidence=m.confidence,
                                    rule=m.rule,
                                    reasons=list(m.reasons),
                                    redacted=m.redacted,
                                    protected=False,
                                )
                            )
                continue

            result.append(m)

        for idx, ps in enumerate(protected_spans):
            if idx in covered_protected_idx:
                continue
            if any(self._spans_overlap(ps.start, ps.end, r.start, r.end) for r in result):
                continue
            result.append(
                SpanMatch(
                    start=ps.start,
                    end=ps.end,
                    text=ps.text,
                    category=Category.NAME,
                    confidence=Confidence.HIGH,
                    rule=ps.reason,
                    reasons=[f"Geschützt: {ps.reason}"],
                    redacted=False,
                    protected=True,
                )
            )

        result.sort(key=lambda m: m.start)
        return result

    def _propagate_cell_names(
        self,
        text: str,
        resolved: list[SpanMatch],
        whitelist: ApplicantWhitelist,
        json_key_spans: list[tuple[int, int]],
    ) -> list[SpanMatch]:
        """Überträgt in derselben Zelle bereits erkannte Namen konsistent auf weitere Vorkommen (auch ALL-CAPS)."""
        candidate_tokens: dict[str, str] = {}
        for m in resolved:
            if m.category != Category.NAME:
                continue
            for raw_tok in m.text.split():
                tok = raw_tok.strip(".,:;!?()[]{}\"'")
                if not tok:
                    continue
                low = tok.lower()
                min_len = 3 if (low in COMMON_FIRST_NAMES or low in COMMON_SURNAMES) else 4
                if len(tok) < min_len:
                    continue
                if not tok.replace("-", "").isalpha():
                    continue
                if (
                    low in NON_NAME_WORDS
                    or low in MAJOR_GERMAN_CITIES
                    or low in ORG_NOISE_WORDS
                    or LEGAL_FORM_TAIL_RE.fullmatch(tok)
                    or matches_job_stem(normalize_job_token(tok))
                    or whitelist.is_name_of_applicant(tok)
                ):
                    continue
                candidate_tokens.setdefault(low, tok)

        if not candidate_tokens:
            return resolved

        extra: list[SpanMatch] = []
        for low_tok, orig_tok in candidate_tokens.items():
            pat = re.compile(rf"\b{re.escape(low_tok)}\b", re.IGNORECASE)
            for hit in pat.finditer(text):
                s, e = hit.start(), hit.end()
                seg = text[s:e]
                # Nur großgeschriebene / ALL-CAPS Vorkommen übernehmen (verhindert kleingeschriebene Verben/Wörter)
                if not seg[0].isupper():
                    continue
                if json_key_spans and any(ks <= s and e <= ke for ks, ke in json_key_spans):
                    continue
                if any(self._spans_overlap(s, e, ex.start, ex.end) for ex in resolved):
                    continue
                if any(self._spans_overlap(s, e, ex.start, ex.end) for ex in extra):
                    continue
                extra.append(
                    SpanMatch(
                        start=s,
                        end=e,
                        text=seg,
                        category=Category.NAME,
                        confidence=Confidence.HIGH,
                        rule=f"Name (Wiederholung im selben Text: {orig_tok})",
                    )
                )

        if not extra:
            return resolved

        combined = sorted(resolved + extra, key=lambda m: m.start)
        # Direkt benachbarte NAME-Treffer (z. B. „CELESTE“ + „MUSTERMANN“) zu einem sauberen Block verschmelzen
        merged: list[SpanMatch] = []
        for m in combined:
            if (
                merged
                and merged[-1].category == Category.NAME
                and m.category == Category.NAME
                and text[merged[-1].end:m.start] == " "
            ):
                prev = merged[-1]
                prev.end = m.end
                prev.text = text[prev.start:prev.end]
                for r in m.reasons:
                    if r not in prev.reasons:
                        prev.reasons.append(r)
                if m.confidence == Confidence.HIGH:
                    prev.confidence = Confidence.HIGH
            else:
                merged.append(m)
        return merged

    def analyze_cell(
        self,
        text: str,
        config: RedactionConfig,
        row_index: int = 0,
        column: str = "",
        ner_matches: list[SpanMatch] | None = None,
        whitelist: ApplicantWhitelist | None = None,
    ) -> CellResult:
        """Analysiert eine einzelne Zelle vollständig und liefert Original, Schwärzung & alle Fundstellen."""
        if not text or not text.strip():
            return CellResult(row_index=row_index, column=column, original=text, redacted=text, matches=[])

        wl = whitelist or ApplicantWhitelist(
            first_name=config.applicant_first_name,
            last_name=config.applicant_last_name,
            extra_terms=config.extra_whitelist,
        )

        raw_matches = run_rule_detectors(text, config)
        if ner_matches is not None:
            raw_matches.extend(ner_matches)
        elif config.use_ner and self.ner.available:
            raw_matches.extend(self.ner.detect(text, config))

        json_key_spans = _find_json_key_spans(text)
        if json_key_spans:
            raw_matches = [
                m
                for m in raw_matches
                if m.category == Category.MANUAL
                or not any(ks <= m.start and m.end <= ke for ks, ke in json_key_spans)
            ]

        resolved = self._resolve_overlaps(text, raw_matches)

        # Intra-Zellen-Namens-Propagation: Wenn ein Name einmal im Text erkannt wurde (z. B. „Celeste“
        # nach „Liebe Grüße“), werden alle weiteren Vorkommen desselben Namens (auch GROSSBUCHSTABEN wie „CELESTE“)
        # im selben Text zuverlässig mitgeschwärzt und mit angrenzenden Namen verschmolzen!
        if config.redact_names and resolved:
            resolved = self._propagate_cell_names(text, resolved, wl, json_key_spans)

        for m in resolved:
            m.redacted = True if m.confidence == Confidence.HIGH else config.redact_uncertain_by_default

        protected_spans = wl.find_protected_spans(text)
        final_matches = self._apply_whitelist(resolved, protected_spans, wl)

        for idx, m in enumerate(final_matches):
            m.match_id = f"r{row_index}_{column}_{idx}"

        redacted_text = apply_redactions_to_text(text, final_matches, config)
        return CellResult(row_index=row_index, column=column, original=text, redacted=redacted_text, matches=final_matches)

    def scan_rows(
        self,
        headers: list[str],
        rows: list[dict[str, str]],
        config: RedactionConfig,
        encoding: str = "utf-8",
        delimiter: str = ";",
        cached_ner_map: dict[tuple[int, str], list[SpanMatch]] | None = None,
        out_ner_map: dict[tuple[int, str], list[SpanMatch]] | None = None,
        out_ner_by_text: dict[str, list[SpanMatch]] | None = None,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[dict[str, str]], list[CellResult], ScanSummary]:
        """Scannt alle ausgewählten Spalten mit Text-Deduplizierung (NER + Regel-Memoization)."""
        target_columns = self._select_columns(headers, config.columns)
        whitelist = ApplicantWhitelist(
            first_name=config.applicant_first_name,
            last_name=config.applicant_last_name,
            extra_terms=config.extra_whitelist,
        )

        # Einzigartige nicht-leere Texte sammeln -> spart sowohl spaCy-Zeit als auch Regel-CPU & RAM!
        unique_texts: list[str] = []
        text_to_uid: dict[str, int] = {}
        non_empty_cells = 0
        for row in rows:
            for col in target_columns:
                val = row.get(col, "") or ""
                if val.strip():
                    non_empty_cells += 1
                    if val not in text_to_uid:
                        text_to_uid[val] = len(unique_texts)
                        unique_texts.append(val)

        ner_active = bool(cached_ner_map is None and config.use_ner and self.ner.available and unique_texts)
        if progress_cb:
            progress_cb({
                "phase": "ner" if ner_active else "rules",
                "phase_label": (
                    f"Lokale Sprach-KI ({self.ner.model_name}) analysiert Texte…"
                    if ner_active
                    else "Regel-Detektoren & Antragsteller-Schutz laufen…"
                ),
                "percent": 10,
                "processed": 0,
                "total": len(rows),
                "redactions_so_far": 0,
                "detail": f"{len(rows):,} Zeilen · {len(unique_texts):,} einzigartige Texte".replace(",", "."),
            })

        ner_by_text: dict[str, list[SpanMatch]] = {}
        if ner_active:
            def _on_ner_progress(done_u: int, total_u: int) -> None:
                if not progress_cb:
                    return
                pct = 10 + int(54 * (done_u / max(1, total_u)))
                progress_cb({
                    "phase": "ner",
                    "phase_label": f"Lokale Sprach-KI ({self.ner.model_name}) erkennt Namen, Orte & Firmen…",
                    "percent": min(64, pct),
                    "processed": done_u,
                    "total": total_u,
                    "redactions_so_far": 0,
                    "detail": f"{done_u:,} / {total_u:,} einzigartige Texte analysiert".replace(",", "."),
                })

            unique_ner = self.ner.detect_batch(
                unique_texts,
                config,
                batch_size=64,
                progress_cb=_on_ner_progress if progress_cb else None,
            )
            for txt, raw_m in zip(unique_texts, unique_ner):
                if raw_m:
                    ner_by_text[txt] = raw_m
            if out_ner_by_text is not None:
                out_ner_by_text.update(ner_by_text)

        # Regel- & Whitelist-Memoization pro einzigartigem Zellentext (massiver Speedup bei CRM-Wiederholungen!)
        use_text_memo = cached_ner_map is None
        analyzed_memo: dict[str, tuple[str, list[SpanMatch]]] = {}

        redacted_rows: list[dict[str, str]] = []
        cell_results: list[CellResult] = []
        rows_with_findings: set[int] = set()
        cat_counter: Counter[str] = Counter()
        total_redactions = 0
        high_redactions = 0
        review_findings = 0
        protected_hits = 0

        total_rows_n = len(rows)
        rules_start_pct = 65 if ner_active else 12
        rules_span_pct = 96 - rules_start_pct
        report_every = max(25, total_rows_n // 60) if total_rows_n else 1

        for r_idx, row in enumerate(rows):
            new_row = dict(row)
            for col in target_columns:
                val = row.get(col, "") or ""
                if not val or not val.strip():
                    continue

                if use_text_memo and val in analyzed_memo:
                    memo_redacted, memo_matches = analyzed_memo[val]
                    if memo_matches:
                        cloned_matches = [_clone_match(m, f"r{r_idx}_{col}_{i}") for i, m in enumerate(memo_matches)]
                        cell = CellResult(r_idx, col, val, memo_redacted, cloned_matches)
                    else:
                        cell = CellResult(r_idx, col, val, memo_redacted, [])
                else:
                    if cached_ner_map is not None:
                        ner_list = [_clone_match(m) for m in cached_ner_map.get((r_idx, col), [])]
                    else:
                        raw_ner = ner_by_text.get(val)
                        ner_list = [_clone_match(m) for m in raw_ner] if raw_ner else []
                    if out_ner_map is not None and ner_list:
                        out_ner_map[(r_idx, col)] = [_clone_match(m) for m in ner_list]

                    cell = self.analyze_cell(
                        text=val,
                        config=config,
                        row_index=r_idx,
                        column=col,
                        ner_matches=ner_list,
                        whitelist=whitelist,
                    )
                    if use_text_memo:
                        analyzed_memo[val] = (cell.redacted, [_clone_match(m) for m in cell.matches])

                new_row[col] = cell.redacted
                if cell.matches:
                    cell_results.append(cell)
                    if any(not m.protected for m in cell.matches):
                        rows_with_findings.add(r_idx)
                    for m in cell.matches:
                        if m.protected:
                            protected_hits += 1
                        else:
                            if m.redacted:
                                total_redactions += 1
                                if m.confidence == Confidence.HIGH:
                                    high_redactions += 1
                                elif m.confidence == Confidence.REVIEW:
                                    review_findings += 1
                                cat_counter[CATEGORY_LABELS_DE.get(m.category, m.category.value)] += 1
            redacted_rows.append(new_row)

            if progress_cb and ((r_idx + 1) % report_every == 0 or r_idx + 1 == total_rows_n):
                done_r = r_idx + 1
                pct = rules_start_pct + int(rules_span_pct * (done_r / max(1, total_rows_n)))
                progress_cb({
                    "phase": "rules",
                    "phase_label": "Prüfe Regeln, Kontodaten, Adressen, Ortschaften & Antragsteller-Schutz…",
                    "percent": min(96, pct),
                    "processed": done_r,
                    "total": total_rows_n,
                    "redactions_so_far": total_redactions,
                    "detail": f"{done_r:,} / {total_rows_n:,} Zeilen geprüft · {total_redactions:,} Schwärzungen".replace(",", "."),
                })

        if progress_cb:
            progress_cb({
                "phase": "finalizing",
                "phase_label": "Bereite Live-Vorschau, Prüfbericht & Exporte vor…",
                "percent": 98,
                "processed": total_rows_n,
                "total": total_rows_n,
                "redactions_so_far": total_redactions,
                "detail": f"{total_rows_n:,} Zeilen fertig · {total_redactions:,} aktive Schwärzungen".replace(",", "."),
            })

        summary = ScanSummary(
            total_rows=len(rows),
            scanned_columns=target_columns,
            rows_with_findings=len(rows_with_findings),
            total_redactions=total_redactions,
            high_confidence_redactions=high_redactions,
            review_findings=review_findings,
            protected_applicant_hits=protected_hits,
            by_category=dict(cat_counter.most_common()),
            ner_active=bool(config.use_ner and self.ner.available),
            ner_model_name=self.ner.model_name,
            encoding=encoding,
            delimiter=delimiter,
        )
        return redacted_rows, cell_results, summary

    @staticmethod
    def _select_columns(headers: list[str], requested: list[str] | None) -> list[str]:
        if requested:
            valid = [c for c in requested if c in headers]
            if valid:
                return valid
        selected = [h for h in headers if _is_recommended_scan_column(h)]
        if selected:
            return selected
        skip_names = {"datum", "date", "zeit", "timestamp", "id", "bestellung", "bestellnummer", "kunde", "kundennummer", "art", "typ", "status"}
        non_id = [h for h in headers if h.strip().lower() not in skip_names]
        return non_id if non_id else list(headers)


_FREE_TEXT_HEADERS = frozenset({
    "notiz", "notizen", "nachricht", "nachrichten", "text", "kommentar", "kommentare",
    "bemerkung", "bemerkungen", "beschreibung", "inhalt", "betreff", "subject", "body", "message", "notes",
})

_PII_HEADER_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("vorname", "Vorname"), ("nachname", "Nachname"), ("familienname", "Nachname"),
    ("kundenname", "Kundenname"), ("ansprechpartner", "Ansprechpartner"),
    ("sachbearbeiter", "Sachbearbeiter"), ("mitarbeiter", "Mitarbeiter"),
    ("absender", "Absender"), ("empfänger", "Empfänger"), ("empfaenger", "Empfänger"),
    ("kontoinhaber", "Kontoinhaber"), ("email", "E-Mail"), ("e-mail", "E-Mail"),
    ("mail", "E-Mail"), ("telefon", "Telefonnummer"), ("rufnummer", "Telefonnummer"),
    ("mobil", "Mobilnummer"), ("handy", "Handynummer"), ("fax", "Faxnummer"),
    ("iban", "IBAN"), ("kontonummer", "Kontonummer"), ("bankverbindung", "Kontodaten"),
    ("adresse", "Adresse"), ("anschrift", "Adresse"), ("straße", "Straße"), ("strasse", "Straße"),
    ("wohnort", "Wohnort"), ("postleitzahl", "PLZ"), ("geburtsdatum", "Geburtsdatum"),
    ("geburtstag", "Geburtsdatum"), ("kennzeichen", "Kfz-Kennzeichen"),
    ("steuernummer", "Steuernummer"), ("steuer-id", "Steuer-ID"), ("ust-id", "USt-IdNr"),
    ("firma", "Firmenname"), ("unternehmen", "Firmenname"),
)

_EXACT_PII_HEADERS: dict[str, str] = {
    "name": "Name", "tel": "Telefonnummer", "tel.": "Telefonnummer", "plz": "PLZ",
    "ort": "Ort", "stadt": "Stadt", "bank": "Bank", "kfz": "Kfz-Kennzeichen",
    "ip": "IP-Adresse", "ip-adresse": "IP-Adresse",
}

_SAMPLE_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,15}\b")
_SAMPLE_IBAN_RE = re.compile(r"\bDE\d{2}[ \t]?(?:\d{4}[ \t]?){4}\d{2}\b", re.IGNORECASE)
_SAMPLE_PHONE_RE = re.compile(r"(?:\+49|0049|01[567]\d|0[2-9]\d{1,4})[ \t/\-]+\d{3,}")


def _pii_header_reason(header: str) -> str | None:
    hl = header.strip().lower()
    if hl in _EXACT_PII_HEADERS:
        return f"Spaltenname „{header}“ ({_EXACT_PII_HEADERS[hl]})"
    for kw, label in _PII_HEADER_KEYWORDS:
        if kw in hl:
            return f"Spaltenname „{header}“ ({label})"
    return None


def _is_recommended_scan_column(header: str) -> bool:
    hl = header.strip().lower()
    return hl in _FREE_TEXT_HEADERS or _pii_header_reason(header) is not None


def detect_pii_warning_columns(headers: list[str], rows: list[dict[str, str]], sample_limit: int = 40) -> dict[str, str]:
    """Prüft Überschriften & Beispielwerte auf mögliche personenbezogene Daten (für UI-Warnung bei Abwahl)."""
    warnings: dict[str, str] = {}
    sample = rows[:sample_limit]
    for h in headers:
        reason = _pii_header_reason(h)
        if reason:
            warnings[h] = reason
            continue
        for row in sample:
            val = (row.get(h, "") or "").strip()
            if not val:
                continue
            if "@" in val and _SAMPLE_EMAIL_RE.search(val):
                warnings[h] = f"Enthält E-Mail-Adressen in „{h}“"
                break
            if _SAMPLE_IBAN_RE.search(val):
                warnings[h] = f"Enthält IBANs in „{h}“"
                break
            if _SAMPLE_PHONE_RE.search(val):
                warnings[h] = f"Enthält Telefonnummern in „{h}“"
                break
    return warnings


def run_residual_pii_check(
    redacted_rows: list[dict[str, str]],
    target_columns: list[str],
    config: RedactionConfig,
    cell_lookup: dict[tuple[int, str], CellResult],
    max_samples: int = 25,
) -> dict[str, Any]:
    """Zweiter Kontroll-Durchlauf über die fertige geschwärzte Ausgabe (mit Text-Memoization)."""
    check_cfg = RedactionConfig(
        applicant_first_name=config.applicant_first_name,
        applicant_last_name=config.applicant_last_name,
        extra_whitelist=list(config.extra_whitelist),
        redact_names=False,
        redact_addresses=config.redact_addresses,
        redact_house_numbers=False,
        redact_postal_codes=False,
        redact_locations=False,
        redact_bank=config.redact_bank,
        redact_job_titles=False,
        redact_amounts=False,
        redact_phones=config.redact_phones,
        redact_emails=config.redact_emails,
        redact_websites=False,
        redact_ip_addresses=config.redact_ip_addresses,
        redact_birthdates=config.redact_birthdates,
        redact_license_plates=config.redact_license_plates,
        redact_tax_ids=config.redact_tax_ids,
        redact_companies=False,
        use_ner=False,
    )
    wl = ApplicantWhitelist(
        first_name=config.applicant_first_name,
        last_name=config.applicant_last_name,
        extra_terms=config.extra_whitelist,
    )

    residual_count = 0
    samples: list[dict[str, Any]] = []
    hits_cache: dict[str, list[SpanMatch]] = {}

    for r_idx, row in enumerate(redacted_rows):
        for col in target_columns:
            val = row.get(col, "") or ""
            if not val.strip():
                continue
            hits = hits_cache.get(val)
            if hits is None:
                raw_hits = [h for h in run_rule_detectors(val, check_cfg) if h.confidence == Confidence.HIGH]
                if raw_hits and wl.active:
                    prot_spans = wl.find_protected_spans(val)
                    if prot_spans:
                        raw_hits = [h for h in raw_hits if not any(ps.start <= h.start and h.end <= ps.end for ps in prot_spans)]
                hits = raw_hits
                hits_cache[val] = hits
            if not hits:
                continue

            cell = cell_lookup.get((r_idx, col))
            released_texts = {m.text.strip().lower() for m in (cell.matches if cell else []) if not m.redacted}
            for h in hits:
                if h.text.strip().lower() in released_texts:
                    continue
                residual_count += 1
                if len(samples) < max_samples:
                    samples.append({
                        "row": r_idx + 2,
                        "column": col,
                        "category": CATEGORY_LABELS_DE.get(h.category, h.category.value),
                        "text": h.text,
                        "rule": h.rule,
                    })

    return {"count": residual_count, "samples": samples}


def recommend_default_columns(headers: list[str]) -> list[str]:
    """Empfiehlt Standard-Spalten für die Auswahl in der Browser-Oberfläche."""
    return RedactionEngine._select_columns(headers, None)
