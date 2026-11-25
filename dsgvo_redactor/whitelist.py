"""Antragsteller-Schutz (Whitelist): schützt den Namen des Ex-Mitarbeiters zuverlässig ohne Dritte zu leaken."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .german_dict import is_common_german_dictionary_word
from .lexicons import COMMON_FIRST_NAMES, COMMON_SURNAMES, NON_NAME_WORDS


_PRECEDING_WORD_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß\-]{1,25})[ \t]+$")
_FOLLOWING_WORD_RE = re.compile(r"^[ \t]+([A-ZÄÖÜ][a-zäöüß\-]{1,25})\b")
_CLEAN_HONORIFIC_RE = re.compile(r"^(?:herrn?|frau|hr\.?|fr\.?|prof\.?|dr\.?)\s+", re.IGNORECASE)
_MULTI_WS_RE = re.compile(r"\s+")
_SIMPLE_WORD_TERM_RE = re.compile(r"^[\wÄÖÜäöüß\-]+$")
_UMLAUT_TO_ASCII = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def _expand_umlaut_variants(name: str) -> list[str]:
    """Erzeugt gängige deutsche Umlaut-Schreibweisen (z. B. Hübner <-> Huebner, Müller <-> Mueller)."""
    if not name:
        return []
    variants: list[str] = [name]
    low = name.lower()
    folded = low
    for uml, asc in _UMLAUT_TO_ASCII:
        folded = folded.replace(uml, asc)
    if folded != low:
        variants.append(folded)
    # Rückrichtung für ASCII-Eingaben wie „Huebner“ -> auch „Hübner“ matchen
    expanded = low
    for uml, asc in _UMLAUT_TO_ASCII:
        if asc in expanded:
            cand = expanded.replace(asc, uml)
            if cand not in variants:
                variants.append(cand)
    return list(dict.fromkeys(variants))
_PRECEDING_NON_FIRST = frozenset({
    "herr", "herrn", "frau", "hr", "fr", "dr", "prof", "sehr", "geehrter",
    "geehrte", "lieber", "liebe", "hallo", "guten", "tag", "an", "von", "mit",
    "bei", "durch", "für", "zu", "händen", "kollege", "kollegin", "sachbearbeiter",
    "sachbearbeiterin", "berater", "beraterin", "ansprechpartner", "ansprechpartnerin",
})


@dataclass(frozen=True)
class ProtectedSpan:
    """Ein geschützter Textbereich, der dem Antragsteller oder der Whitelist zugeordnet ist."""

    start: int
    end: int
    text: str
    reason: str


class ApplicantWhitelist:
    """Erkennt alle zulässigen Schreibweisen des Antragstellers (und benutzerdefinierte Ausnahmen).

    Schützt:
    - Vollname: „Vorname Nachname“, „Nachname, Vorname“
    - Mit Anrede/Titel: „Herr Nachname“, „Herrn Nachname“, „Frau Nachname“, „Herr Dr. Nachname“
    - Initialen-Form: „V. Nachname“, „Vorname N.“
    - Alleinstehender Nachname (solange NICHT ein anderer Vorname davor steht!)
    - Alleinstehender Vorname (solange NICHT ein anderer Nachname danach steht!)
    - Benutzerdefinierte Zusatzbegriffe (z. B. Geburtsname, Kürzel, eigene Firmen-E-Mail)
    """

    def __init__(
        self,
        first_name: str = "",
        last_name: str = "",
        extra_terms: list[str] | None = None,
    ) -> None:
        self.first_name = first_name.strip()
        self.last_name = last_name.strip()
        self.extra_terms = [t.strip() for t in (extra_terms or []) if t and t.strip()]
        self._first_parts = [p for p in re.split(r"[\s\-]+", self.first_name) if p]
        self._last_parts = [p for p in re.split(r"[\s\-]+", self.last_name) if p]
        self._first_variants_l = {v.lower() for v in _expand_umlaut_variants(self.first_name)}
        for p in self._first_parts:
            if len(p) >= 2:
                self._first_variants_l.update(v.lower() for v in _expand_umlaut_variants(p))
        self._last_variants_l = {v.lower() for v in _expand_umlaut_variants(self.last_name)}
        for p in self._last_parts:
            if len(p) >= 2:
                self._last_variants_l.update(v.lower() for v in _expand_umlaut_variants(p))

        self._full_patterns: list[tuple[re.Pattern[str], str]] = []
        self._standalone_first_re: re.Pattern[str] | None = None
        self._standalone_last_re: re.Pattern[str] | None = None
        self._extra_patterns: list[tuple[re.Pattern[str], str]] = []

        self._compile_patterns()

    @property
    def active(self) -> bool:
        return bool(self.first_name or self.last_name or self.extra_terms)

    def _compile_patterns(self) -> None:
        # Auch Umlaut-Varianten & einzelne Teile bei Doppelnamen berücksichtigen (z.B. „Hübner/Huebner“, „Anna-Lena“ -> „Anna“)
        first_variants: list[str] = []
        if self.first_name:
            first_variants.extend(re.escape(v) for v in _expand_umlaut_variants(self.first_name))
            if len(self._first_parts) > 1:
                for p in self._first_parts:
                    if len(p) >= 2:
                        first_variants.extend(re.escape(v) for v in _expand_umlaut_variants(p))
        first_alt = "|".join(dict.fromkeys(first_variants))

        last_variants: list[str] = []
        if self.last_name:
            last_variants.extend(re.escape(v) for v in _expand_umlaut_variants(self.last_name))
        ln = f"(?:{'|'.join(dict.fromkeys(last_variants))})" if last_variants else ""

        honorific = r"(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)\s+)?(?:(?:Prof\.?\s*)?(?:Dr\.?\s*)?(?:med\.?\s*|jur\.?\s*|rer\.?\s*nat\.?\s*)?)?"

        if first_alt and ln:
            # 1. [Anrede/Titel] Vorname Nachname
            self._full_patterns.append((
                re.compile(rf"\b{honorific}(?:{first_alt})\s+{ln}\b", re.IGNORECASE),
                "Antragsteller (Vollname)",
            ))
            # 2. Nachname, Vorname
            self._full_patterns.append((
                re.compile(rf"\b{ln},\s*(?:{first_alt})\b", re.IGNORECASE),
                "Antragsteller (Nachname, Vorname)",
            ))
            # 3. Initiale + Nachname (z.B. M. Mustermann oder Herr M. Mustermann)
            first_init = re.escape(self.first_name[0])
            self._full_patterns.append((
                re.compile(rf"\b{honorific}{first_init}\.?\s+{ln}\b", re.IGNORECASE),
                "Antragsteller (Initiale + Nachname)",
            ))
            # 4. Vorname + Initiale Nachname (z.B. Max M.)
            last_init = re.escape(self.last_name[0])
            self._full_patterns.append((
                re.compile(rf"\b(?:{first_alt})\s+{last_init}\.", re.IGNORECASE),
                "Antragsteller (Vorname + Initiale)",
            ))

        if ln:
            # 5. Anrede + Nachname (z.B. Herr Mustermann, Frau Dr. Mustermann)
            self._full_patterns.append((
                re.compile(
                    rf"\b(?:Herrn?|Frau|Hr\.?|Fr\.?)\s+(?:(?:Prof\.?\s*)?(?:Dr\.?\s*)?)?{ln}\b",
                    re.IGNORECASE,
                ),
                "Antragsteller (Anrede + Nachname)",
            ))
            # 6. Alleinstehender Nachname
            self._standalone_last_re = re.compile(rf"\b{ln}\b", re.IGNORECASE)

        if first_alt:
            # 7. Alleinstehender Vorname
            self._standalone_first_re = re.compile(rf"\b(?:{first_alt})\b", re.IGNORECASE)

        # Benutzerdefinierte Zusatzbegriffe (z. B. Kürzel, Geburtsname, eigene Mail)
        for term in self.extra_terms:
            esc = re.escape(term)
            if _SIMPLE_WORD_TERM_RE.match(term):
                pat = re.compile(rf"\b{esc}\b", re.IGNORECASE)
            else:
                pat = re.compile(esc, re.IGNORECASE)
            self._extra_patterns.append((pat, f"Ausnahme-Liste ({term})"))

    def _is_preceded_by_other_first_name(self, text: str, start: int) -> bool:
        """Prüft, ob vor einem Nachnamen ein ANDERER Vorname steht (z.B. ‚Klaus Mustermann‘ bei Antragsteller ‚Max Mustermann‘)."""
        prefix = text[max(0, start - 35):start]
        m = _PRECEDING_WORD_RE.search(prefix)
        if not m:
            return False
        word_lower = m.group(1).lower()
        if self.first_name and word_lower in self._first_variants_l:
            return False
        if word_lower in _PRECEDING_NON_FIRST or word_lower in NON_NAME_WORDS:
            return False
        return word_lower in COMMON_FIRST_NAMES

    def _is_followed_by_other_surname(self, text: str, end: int) -> bool:
        """Prüft, ob nach dem Vornamen des Antragstellers ein ANDERER Nachname steht (z.B. ‚Max Meier‘)."""
        suffix = text[end:min(len(text), end + 35)]
        m = _FOLLOWING_WORD_RE.match(suffix)
        if not m:
            return False
        word = m.group(1)
        word_lower = word.lower()
        if self.last_name and word_lower in self._last_variants_l:
            return False
        if word_lower in COMMON_SURNAMES:
            return True
        if word_lower in NON_NAME_WORDS or is_common_german_dictionary_word(word):
            return False
        return not word.isupper() and len(word) >= 3

    def find_protected_spans(self, text: str) -> list[ProtectedSpan]:
        """Findet alle geschützten Textbereiche im gegebenen Text."""
        if not self.active or not text:
            return []

        raw_spans: list[ProtectedSpan] = []

        # 1. Spezifische Voll-/Anrede-/Initialen-Muster
        for pat, reason in self._full_patterns:
            for m in pat.finditer(text):
                raw_spans.append(ProtectedSpan(m.start(), m.end(), m.group(0), reason))

        # 2. Zusatzbegriffe
        for pat, reason in self._extra_patterns:
            for m in pat.finditer(text):
                raw_spans.append(ProtectedSpan(m.start(), m.end(), m.group(0), reason))

        # Hilfsfunktion: liegt Span bereits in einem Vollmuster?
        def covered(s: int, e: int) -> bool:
            return any(ps.start <= s and e <= ps.end for ps in raw_spans)

        # 3. Alleinstehender Nachname (nur wenn kein fremder Vorname davor steht)
        if self._standalone_last_re:
            for m in self._standalone_last_re.finditer(text):
                if covered(m.start(), m.end()):
                    continue
                if self._is_preceded_by_other_first_name(text, m.start()):
                    continue
                raw_spans.append(
                    ProtectedSpan(m.start(), m.end(), m.group(0), "Antragsteller (Nachname)")
                )

        # 4. Alleinstehender Vorname (nur wenn kein fremder Nachname danach steht)
        if self._standalone_first_re:
            for m in self._standalone_first_re.finditer(text):
                if covered(m.start(), m.end()):
                    continue
                if self._is_followed_by_other_surname(text, m.end()):
                    continue
                raw_spans.append(
                    ProtectedSpan(m.start(), m.end(), m.group(0), "Antragsteller (Vorname)")
                )

        # Sortieren und überlappende geschützte Spans deduplizieren (längster gewinnt)
        raw_spans.sort(key=lambda sp: (sp.start, -(sp.end - sp.start)))
        merged: list[ProtectedSpan] = []
        for sp in raw_spans:
            if not merged or sp.start >= merged[-1].end:
                merged.append(sp)
            elif sp.end > merged[-1].end:
                merged[-1] = ProtectedSpan(
                    merged[-1].start,
                    sp.end,
                    text[merged[-1].start:sp.end],
                    merged[-1].reason,
                )
        return merged

    def is_name_of_applicant(self, candidate: str) -> bool:
        """Schnellcheck, ob ein erkannter Name exakt zum Antragsteller gehört."""
        if not self.active:
            return False
        cleaned = _MULTI_WS_RE.sub(" ", _CLEAN_HONORIFIC_RE.sub("", candidate.strip())).strip(".,:;!?()[]{}\"'<> ")
        cleaned_l = cleaned.lower()

        if self._first_variants_l and self._last_variants_l:
            for fn_v in self._first_variants_l:
                for ln_v in self._last_variants_l:
                    if cleaned_l in {f"{fn_v} {ln_v}", f"{ln_v}, {fn_v}", f"{fn_v[0]}. {ln_v}", f"{fn_v[0]} {ln_v}"}:
                        return True
        if self._last_variants_l and cleaned_l in self._last_variants_l:
            return True
        if self._first_variants_l and cleaned_l in self._first_variants_l:
            return True
        for extra in self.extra_terms:
            if cleaned_l == extra.lower():
                return True
        return False
