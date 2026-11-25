"""Lokales Offline-NER-Modul (spaCy Deutsch) – speicherschonend, vorkompiliert & 100 % offline."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Callable

from .detectors_common import has_digit, is_german_compound_non_entity, is_public_authority
from .german_dict import is_common_german_dictionary_word
from .lexicons import (
    COMMON_FIRST_NAMES,
    COMMON_SURNAMES,
    COUNTRIES_AND_REGIONS_NON_TOWN,
    MAJOR_GERMAN_CITIES,
    NON_NAME_WORDS,
    ORG_NOISE_WORDS,
)
from .models import Category, Confidence, RedactionConfig, SpanMatch

PREFERRED_MODELS = ("de_core_news_lg", "de_core_news_md", "de_core_news_sm")

_IMAGE_FILENAME_RE = re.compile(r"\.(?:jpe?g|png|gif|pdf|docx?|xlsx?|csv|txt)$", re.IGNORECASE)
_ORDER_CODE_RE = re.compile(
    r"^(?:[A-Za-zÄÖÜäöüß]{1,15}[-_/#]?\d[\dA-Za-z\-_/]*|\d+[-_/][A-Za-z0-9\-_/]+)$"
)
# Technische Bezeichner wie camelCase („sendTemplateMail“, „getUserById“) oder snake_case („bcc_mail“, „customer_ref“)
_CAMEL_OR_SNAKE_CODE_RE = re.compile(r"^(?:[a-zäöüß]+[A-ZÄÖÜ][A-Za-z0-9]*|[A-Za-z0-9]+_[A-Za-z0-9_]+)$")
_NUMERIC_ONLY_RE = re.compile(r"^[\d\s.,:/\-+]+$")
_HONORIFIC_STRIP_RE = re.compile(r"^(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+", re.IGNORECASE)
_TAIL_WORD_RE = re.compile(r"[ \t]+(\S+)$")
_LEGAL_FORM_RE = re.compile(
    r"\b(?:GmbH|UG|AG|OHG|KG|GbR|e\.?\s*K\.?|e\.?\s*V\.?|SE|Ltd\.?|Inc\.?|mbH|PartG|KGaA)\b"
)
_LEGAL_FORM_FOLLOW_RE = re.compile(
    r"^[ \t]+(?:(?:&\s*Co\.?[ \t]*)?(?:GmbH|UG|AG|OHG|KG|GbR|e\.?\s*K\.?|e\.?\s*V\.?|SE|Ltd\.?|Inc\.?|mbH|PartG|KGaA))\b"
)
_PUBLIC_AUTH_BEFORE_LOC_RE = re.compile(
    r"(?i)\b(?:Stadt|Gemeinde|Kreis|Landkreis|Bezirksamt|Ordnungsamt|Bürgeramt|"
    r"Straßenverkehrsamt|Strassenverkehrsamt|Tiefbauamt|Bauamt|Finanzamt|Polizei|"
    r"Polizeipräsidium|Landratsamt|Stadtverwaltung|Gemeindeverwaltung|Kreisverwaltung|"
    r"Bezirksregierung|Senatsverwaltung|Magistrat|Rathaus|Landesamt|Bundesamt|"
    r"Amt[ \t]+für[ \t]+\w+)(?:[ \t]+(?:der[ \t]+|des[ \t]+)?)?[ \t]+\Z"
)
_LOC_CONTEXT_BEFORE_RE = re.compile(
    r"(?i)(?:\b(?:in|aus|nach|bei|von|um|Richtung|Raum|Bereich|Umgebung|Ort|Wohnort|Ortschaft|Gemeinde|Stadtteil)[ \t]*[:=]?[ \t]+|,[ \t]+)\Z"
)
_ALPHA_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")
# Schneidet HTML-Tags & angeschnittene Tag-Reste aus NER-Spans heraus (z. B. „Max Mustermann</span></font>...<tr><td“)
_HTML_TAG_OR_FRAG_RE = re.compile(r"<[^>]*>|<[^>\n]*$|^[^<\n]*>")

_NON_ENTITY_STTS_PREFIXES = (
    "ADJ", "VV", "VA", "VM", "ADV", "APPR", "ART", "KO", "P", "CARD", "PTK",
)
# Typische Adress-/Wohnungs-Abkürzungen, die spaCy manchmal als LOC rät („Hnr“, „Whg“, „OG“, „EG“)
_ADDRESS_ABBREV_NON_LOC = frozenset({
    "hnr", "hausnr", "whg", "etg", "og", "eg", "dg", "ug", "str", "plz",
    "tel", "fax", "mob", "zhd", "app", "apt", "nr", "abs", "empf",
})

_is_german_compound_non_entity = is_german_compound_non_entity


def _segment_has_only_non_entity_pos(doc: Any, start: int, end: int, *, reject_common_noun: bool = False) -> bool:
    """Prüft per spaCy POS-Tagger, ob eine Einzelwort-Entität nur Adjektiv/Verb/Funktionswort (oder NN-Sachwort) ist."""
    span = doc.char_span(start, end, alignment_mode="contract")
    if span is None or len(span) != 1:
        return False
    tag = getattr(span[0], "tag_", "") or ""
    if not tag:
        return False
    if reject_common_noun and tag == "NN":
        return True
    return tag.startswith(_NON_ENTITY_STTS_PREFIXES)


def _emit_clean_subsegments(
    doc_text: str,
    seg_start: int,
    part_clean: str,
    out: list[tuple[int, int, str]],
) -> None:
    """Teilt ein Zeilensegment zusätzlich an HTML-Tags (<...>) auf und trimmt Whitespace exakt."""
    if "<" not in part_clean and ">" not in part_clean:
        if not part_clean.strip():
            return
        lead = len(part_clean) - len(part_clean.lstrip())
        trail = len(part_clean) - len(part_clean.rstrip())
        s = seg_start + lead
        e = seg_start + len(part_clean) - trail
        if s < e:
            out.append((s, e, doc_text[s:e]))
        return

    pos = 0
    for m in _HTML_TAG_OR_FRAG_RE.finditer(part_clean):
        if m.start() > pos:
            chunk = part_clean[pos:m.start()]
            if chunk.strip():
                lead = len(chunk) - len(chunk.lstrip())
                trail = len(chunk) - len(chunk.rstrip())
                s = seg_start + pos + lead
                e = seg_start + m.start() - trail
                if s < e:
                    out.append((s, e, doc_text[s:e]))
        pos = m.end()
    if pos < len(part_clean):
        chunk = part_clean[pos:]
        if chunk.strip():
            lead = len(chunk) - len(chunk.lstrip())
            trail = len(chunk) - len(chunk.rstrip())
            s = seg_start + pos + lead
            e = seg_start + len(part_clean) - trail
            if s < e:
                out.append((s, e, doc_text[s:e]))


def _iter_line_segments(doc_text: str, ent_start: int, ent_end: int) -> list[tuple[int, int, str]]:
    """Teilt eine NER-Entität an Zeilenumbrüchen & HTML-Tags auf, damit Namen sauber abgegrenzt bleiben."""
    raw_slice = doc_text[ent_start:ent_end]
    segments: list[tuple[int, int, str]] = []
    if "\n" not in raw_slice and "\r" not in raw_slice:
        _emit_clean_subsegments(doc_text, ent_start, raw_slice, segments)
        return segments

    cursor = ent_start
    for part in raw_slice.splitlines(keepends=True):
        part_clean = part.rstrip("\r\n")
        if part_clean.strip():
            _emit_clean_subsegments(doc_text, cursor, part_clean, segments)
        cursor += len(part)
    return segments


class LocalGermanNER:
    """Wrapper um das lokale deutsche spaCy-Sprachmodell mit automatischer Erkennung & sauberem Fallback."""

    def __init__(self, model_name: str | None = None) -> None:
        self.nlp: Any = None
        self.model_name: str = "nicht installiert"
        self.available: bool = False
        self._load(model_name)

    def _load(self, preferred: str | None = None) -> None:
        try:
            import spacy  # type: ignore
        except ImportError:
            self.available = False
            self.model_name = "spacy nicht installiert"
            return

        candidates = [preferred] if preferred else list(PREFERRED_MODELS)
        for name in candidates:
            if not name:
                continue
            try:
                self.nlp = spacy.load(name, exclude=["parser", "lemmatizer", "attribute_ruler"])
                self.model_name = name
                self.available = True
                return
            except OSError:
                continue

        self.available = False
        self.model_name = "kein deutsches spaCy-Modell gefunden"

    @staticmethod
    def _is_noise_entity(raw: str) -> bool:
        cleaned = raw.strip(" \t\n\r.,:;!?()[]{}\"'")
        if (
            len(cleaned) < 2
            or "<" in cleaned
            or ">" in cleaned
            or "=" in cleaned
            or _NUMERIC_ONLY_RE.match(cleaned)
            or _IMAGE_FILENAME_RE.search(cleaned)
            or _ORDER_CODE_RE.match(cleaned)
            or _CAMEL_OR_SNAKE_CODE_RE.match(cleaned)
        ):
            return True
        tokens = [t.strip(".,:;!?()[]{}\"'").lower() for t in cleaned.split() if t.strip()]
        if not tokens:
            return True
        if cleaned.lower() in MAJOR_GERMAN_CITIES:
            return False
        return all(t in NON_NAME_WORDS and t not in MAJOR_GERMAN_CITIES for t in tokens)

    def _extract_from_doc(self, doc: Any, enabled: set[Category]) -> list[SpanMatch]:
        matches: list[SpanMatch] = []
        doc_text = doc.text

        for ent in doc.ents:
            if not ent.text or not ent.text.strip():
                continue
            label = ent.label_
            if label == "PER" and Category.NAME not in enabled:
                continue
            if label == "ORG" and Category.COMPANY not in enabled:
                continue
            if label == "LOC" and Category.LOCATION not in enabled:
                continue
            if label not in ("PER", "ORG", "LOC"):
                continue

            for start, end, text_val in _iter_line_segments(doc_text, ent.start_char, ent.end_char):
                if not text_val or self._is_noise_entity(text_val):
                    continue

                if label == "PER":
                    if has_digit(text_val):
                        continue
                    lead_trim = len(text_val) - len(text_val.lstrip("([{\"«' "))
                    start += lead_trim
                    text_val = text_val[lead_trim:]
                    had_trailing_colon = text_val.endswith(":") or doc_text[end:end + 1] == ":"
                    trimmed_val = text_val.rstrip(".,:;!?)]}\"»' ")
                    if not trimmed_val:
                        continue
                    end = start + len(trimmed_val)
                    text_val = trimmed_val
                    if self._is_noise_entity(text_val):
                        continue
                    if _LEGAL_FORM_RE.search(text_val) or _LEGAL_FORM_FOLLOW_RE.match(doc_text[end:]):
                        continue
                    if text_val.lower() in MAJOR_GERMAN_CITIES or is_public_authority(text_val):
                        continue
                    if text_val.isupper() and len(text_val) <= 5:
                        continue

                    stripped = _HONORIFIC_STRIP_RE.sub("", text_val)
                    if not stripped:
                        continue
                    start += len(text_val) - len(stripped)
                    text_val = stripped

                    while True:
                        m_tail = _TAIL_WORD_RE.search(text_val)
                        if not m_tail:
                            break
                        tail_l = m_tail.group(1).lower().rstrip(".,:;!?")
                        if tail_l in NON_NAME_WORDS and tail_l not in COMMON_SURNAMES:
                            text_val = text_val[:m_tail.start()].rstrip(" \t.,:;!?")
                        else:
                            break
                    if not text_val:
                        continue
                    end = start + len(text_val)

                    words = text_val.split()
                    if not words:
                        continue
                    low_full = text_val.lower().rstrip(".,:;!?")
                    if low_full in NON_NAME_WORDS and low_full not in COMMON_SURNAMES:
                        continue
                    if len(words) == 1 and low_full not in COMMON_FIRST_NAMES and low_full not in COMMON_SURNAMES:
                        # Einzelnes kleingeschriebenes Wort ohne Anrede („konk“, „zusatzrechnung“, „tourenplanung“) ist kein NER-PER
                        if text_val[0].islower():
                            continue
                        if (
                            had_trailing_colon
                            or is_common_german_dictionary_word(text_val)
                            or _segment_has_only_non_entity_pos(doc, start, end, reject_common_noun=True)
                        ):
                            continue

                    conf = Confidence.HIGH if (
                        len(words) >= 2 or words[0].lower() in COMMON_FIRST_NAMES or words[0].lower() in COMMON_SURNAMES
                    ) else Confidence.REVIEW
                    matches.append(SpanMatch(start, end, doc_text[start:end], Category.NAME, conf, f"Lokale Sprach-KI ({self.model_name}: PER)"))

                elif label == "ORG":
                    lead_trim = len(text_val) - len(text_val.lstrip("([{\"«' "))
                    start += lead_trim
                    text_val = text_val[lead_trim:]
                    trimmed_val = text_val.rstrip(".,:;!?)]}\"»' ")
                    if not trimmed_val or len(trimmed_val) < 2:
                        continue
                    end = start + len(trimmed_val)
                    text_val = trimmed_val
                    low_org = text_val.lower()
                    if (
                        is_public_authority(text_val)
                        or low_org in MAJOR_GERMAN_CITIES
                        or low_org in NON_NAME_WORDS
                        or low_org in ORG_NOISE_WORDS
                        or low_org in COUNTRIES_AND_REGIONS_NON_TOWN
                        or (text_val.isupper() and len(text_val) <= 4)
                        or _IMAGE_FILENAME_RE.search(text_val)
                    ):
                        continue
                    if " " not in text_val and (
                        is_common_german_dictionary_word(text_val)
                        or _segment_has_only_non_entity_pos(doc, start, end)
                    ):
                        continue
                    conf = Confidence.HIGH if (_LEGAL_FORM_RE.search(text_val) or "&" in text_val) else Confidence.REVIEW
                    matches.append(SpanMatch(start, end, doc_text[start:end], Category.COMPANY, conf, f"Lokale Sprach-KI ({self.model_name}: ORG)"))

                elif label == "LOC":
                    if has_digit(text_val) or not text_val[0].isupper():
                        continue
                    trimmed_val = text_val.rstrip(".,:;!?")
                    if not trimmed_val or len(trimmed_val) < 3:
                        continue
                    end = start + len(trimmed_val)
                    text_val = trimmed_val
                    low = text_val.lower()
                    if is_public_authority(text_val) or low in COUNTRIES_AND_REGIONS_NON_TOWN:
                        continue
                    line_before = doc_text[max(0, start - 40):start].rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
                    if _PUBLIC_AUTH_BEFORE_LOC_RE.search(line_before):
                        continue
                    if _LEGAL_FORM_RE.search(text_val) or _LEGAL_FORM_FOLLOW_RE.match(doc_text[end:]):
                        continue
                    if (
                        (text_val.isupper() and len(text_val) <= 4)
                        or low in _ADDRESS_ABBREV_NON_LOC
                        or (low in NON_NAME_WORDS and low not in MAJOR_GERMAN_CITIES)
                    ):
                        continue
                    if " " not in text_val and low not in MAJOR_GERMAN_CITIES:
                        if (
                            is_common_german_dictionary_word(text_val)
                            or _segment_has_only_non_entity_pos(doc, start, end, reject_common_noun=True)
                        ):
                            continue
                    conf = Confidence.HIGH if (
                        low in MAJOR_GERMAN_CITIES or "-" in text_val or " " in text_val or _LOC_CONTEXT_BEFORE_RE.search(line_before)
                    ) else Confidence.REVIEW
                    matches.append(SpanMatch(start, end, doc_text[start:end], Category.LOCATION, conf, f"Lokale Sprach-KI ({self.model_name}: LOC)"))

        return matches

    def detect(self, text: str, config: RedactionConfig) -> list[SpanMatch]:
        if not self.available or not config.use_ner or not text or len(text) < 3 or not _ALPHA_RE.search(text):
            return []
        doc = self.nlp(text)
        return self._extract_from_doc(doc, config.enabled_categories())

    def detect_batch(
        self,
        texts: list[str],
        config: RedactionConfig,
        batch_size: int = 64,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> list[list[SpanMatch]]:
        """Speicherschonende Batch-Verarbeitung mit spaCy nlp.pipe (überspringt reine Zahlen/Codes)."""
        if not self.available or not config.use_ner:
            return [[] for _ in texts]

        enabled = config.enabled_categories()
        if not ({Category.NAME, Category.COMPANY, Category.LOCATION} & enabled):
            return [[] for _ in texts]

        total = len(texts)
        results: list[list[SpanMatch]] = [[] for _ in range(total)]

        # Nur Texte mit Buchstaben durch spaCy schicken (überspringt reine Zahlen, Codes, Daten)
        active_indices: list[int] = []
        active_texts: list[str] = []
        for idx, t in enumerate(texts):
            if len(t) >= 3 and _ALPHA_RE.search(t):
                active_indices.append(idx)
                active_texts.append(t)

        if not active_texts:
            if progress_cb:
                progress_cb(total, total)
            return results

        step = max(24, batch_size // 2)
        active_total = len(active_texts)
        for pos, doc in enumerate(self.nlp.pipe(active_texts, batch_size=batch_size), start=1):
            results[active_indices[pos - 1]] = self._extract_from_doc(doc, enabled)
            if progress_cb and (pos % step == 0 or pos == active_total):
                progress_cb(pos, active_total)
        return results
