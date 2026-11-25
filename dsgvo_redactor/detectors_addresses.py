"""Detektoren für Adressen, Straßen, Hausnummern, Postleitzahlen (DE/AT/CH) & Ortschaften."""

from __future__ import annotations

import re

from .detectors_common import has_digit
from .lexicons import (
    AMBIGUOUS_CITY_WORDS,
    COUNTRIES_AND_REGIONS_NON_TOWN,
    MAJOR_GERMAN_CITIES,
    NON_NAME_WORDS,
    STREET_PREFIX_PHRASES,
    STREET_SUFFIXES_COMPOUND,
)
from .models import Category, Confidence, SpanMatch
from .validators import validate_german_plz

# --- Straßen, Hausnummern & PLZ ---

_STREET_SUFFIX_ALT = "|".join(sorted((re.escape(s) for s in STREET_SUFFIXES_COMPOUND), key=len, reverse=True))
_STREET_SUFFIX_CAP_ALT = "|".join(sorted((re.escape(s[0].upper() + s[1:]) for s in STREET_SUFFIXES_COMPOUND), key=len, reverse=True))
_HOUSE_NUM_PART = (
    r"\d{1,4}[ \t]*[a-zA-Z]?(?:[ \t]*[-/][ \t]*\d{1,4}[ \t]*[a-zA-Z]?)?"
    r"(?![ \t]*(?:,\d|\.\d{2}|:\d{2}|t\b|to\b|Tonnen?\b|kg\b|€|EUR\b|Euro\b|Uhr\b|%|Meter\b|cm\b|km\b|Tage?\b|Std\b|Stunden?\b))"
)

STREET_COMPOUND_RE = re.compile(
    rf"\b(?:(?:Alt|Neu|Ober|Unter|Groß|Klein|Sankt|St\.)[a-zäöüß]*[ \t]+)?"
    rf"(?:"
    rf"(?:[A-ZÄÖÜ][a-zäöüßéèê]+(?:-[A-ZÄÖÜa-zäöüß]+)*[ \t]+){{0,2}}[A-ZÄÖÜ][a-zäöüßéèê\-]+(?:{_STREET_SUFFIX_ALT})"
    rf"|"
    rf"(?:[A-ZÄÖÜ][a-zäöüßéèê]+(?:-[A-ZÄÖÜa-zäöüß]+)*[ \t]+){{1,2}}(?:{_STREET_SUFFIX_CAP_ALT})"
    rf")"
    rf"(?:(?:[ \t]+|(?<=\.)|(?<=straße)|(?<=strasse))(?:Nr\.?[ \t]*)?{_HOUSE_NUM_PART})",
    re.UNICODE,
)

_STREET_PREFIX_ALT = "|".join(sorted((re.escape(p) for p in STREET_PREFIX_PHRASES), key=len, reverse=True))
STREET_PREFIX_RE = re.compile(
    rf"\b(?:{_STREET_PREFIX_ALT})[ \t]+[A-ZÄÖÜ][a-zäöüß\-]+(?:[ \t]+[A-ZÄÖÜ][a-zäöüß\-]+){{0,2}}[ \t]+(?:Nr\.?[ \t]*)?{_HOUSE_NUM_PART}",
    re.UNICODE,
)

_LEADING_PREP_STRIP_RE = re.compile(
    r"^(?:Für|In|An|Auf|Bei|Nach|Von|Zu|Zum|Zur|Über|Unter|Durch|Mit|Aus|Ab|Bis|Der|Die|Das|Den|Dem|Eine?m?|Eine?r?)\s+"
)
_ADDRESS_LABEL_RE = re.compile(
    r"(?i)\b(?:Adresse|Anschrift|Straße|Strasse|Wohnadresse|Wohnanschrift|Lieferadresse|"
    r"Rechnungsadresse|Einsatzort|Aufstellort|Standort)[ \t]*[:=][ \t]*([^\n;]{4,90})"
)
_HOUSENUMBER_EXPLICIT_RE = re.compile(
    rf"(?i)\b(?:Hausnummer|Hausnr\.?|Haus-Nr\.?|Hnr\.?)[ \t]*[:=]?[ \t]*({_HOUSE_NUM_PART})"
)
_HOUSENUMBER_NR_CONTEXT_RE = re.compile(
    rf"(?i)(?:Straße|Strasse|Str\.|Haus|Gebäude|Wohnung|Grundstück|Einfahrt|Ecke)[ \t]+Nr\.?[ \t]*({_HOUSE_NUM_PART})"
)

_PLZ_EXPLICIT_RE = re.compile(r"(?i)\b(?:PLZ|Postleitzahl)[ \t]*[:=]?[ \t]*(\d{4,5})\b")
_PLZ_COUNTRY_CITY_RE = re.compile(
    r"\b(?:D|DE)-(\d{5})(?:[ \t]+([A-ZÄÖÜ][a-zäöüß\-]+(?:[ \t]+(?:am|an|im|in|ob|vor|bei)[ \t]+(?:der[ \t]+|dem[ \t]+|den[ \t]+)?[A-ZÄÖÜ][a-zäöüß\-]+)?))?"
)
_PLZ_AT_CH_COUNTRY_CITY_RE = re.compile(
    r"\b(?:A|AT|CH)-([1-9]\d{3})(?:[ \t]+([A-ZÄÖÜ][a-zäöüß\-]+(?:[ \t]+(?:am|an|im|in|ob|vor|bei)[ \t]+(?:der[ \t]+|dem[ \t]+|den[ \t]+)?[A-ZÄÖÜ][a-zäöüß\-]+)?))?"
)
_PLZ_CITY_RE = re.compile(
    r"(?<!\d)(\d{5})[ \t]+([A-ZÄÖÜ][a-zäöüß\-]+(?:[ \t]+(?:am|an|im|in|ob|vor|bei)[ \t]+(?:der[ \t]+|dem[ \t]+|den[ \t]+)?[A-ZÄÖÜ][a-zäöüß\-]+)?)"
)
_PLZ_STANDALONE_RE = re.compile(r"(?<![\d\-/A-Za-z])(\d{5})(?![\d\-/A-Za-z])")
_PLZ_UNIT_AFTER_RE = re.compile(r"^\s*(?:€|EUR\b|Euro\b|km\b|kg\b|t\b|Meter\b)", re.IGNORECASE)
_STREET_ALPHA_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")

_NOT_A_CITY_AFTER_PLZ = frozenset({
    "euro", "eur", "chf", "usd", "stück", "meter", "km", "kg", "tonnen", "tage", "stunden",
    "minuten", "uhr", "bestellung", "auftrag", "kunde", "kunden", "rechnung", "beleg",
    "prozent", "mal", "punkte", "zeichen", "zeilen", "wörter", "seiten", "bilder", "fotos",
    "schilder", "kartons", "kisten", "fahrzeuge", "lkw", "pkw",
})
_NON_PLZ_BEFORE_RE = re.compile(
    r"(?i)(?:Bestell(?:ung|nummer|nr\.?)|Auftrag(?:snummer|snr\.?)?|Kunde(?:nnummer|nnr\.?)?|"
    r"Kd\.?-?Nr\.?|Rechnung(?:snummer|snr\.?)?|Vorgang(?:snummer)?|Ticket|Ref\.?|ID|Nr\.?|No\.?|"
    r"Tel\.?|Fax|Mobil|Konto|BLZ|IBAN|Betrag|Summe|Kosten|Preis|HV\d*)\s*[:=#\-]?\s*$"
)
_FALSE_STREET_WORDS = frozenset({
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag",
    "januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
    "oktober", "november", "dezember", "tag", "datum", "termin", "uhr", "fall", "ende",
    "anfang", "auftrag", "bestellung", "antrag", "genehmigung", "lkw", "pkw", "fahrzeug",
})
_STREET_SKIP_PREFIXES = frozenset({
    "am", "an", "der", "den", "dem", "auf", "im", "in", "vor", "unter", "über",
    "zum", "zur", "bei", "beim", "hinter", "zwischen", "nr",
})
_STREET_BAD_TAILS = frozenset({
    "tag", "monat", "jahr", "stunde", "minute", "termin", "auftrag", "mal", "teil", "punkt", "seite", "bild", "foto",
})


def is_plausible_street(street_text: str) -> bool:
    tokens = _STREET_ALPHA_RE.findall(street_text)
    if not tokens:
        return False
    non_prefix = [t.lower() for t in tokens if t.lower() not in _STREET_SKIP_PREFIXES]
    if not non_prefix or all(w in _FALSE_STREET_WORDS for w in non_prefix):
        return False
    if non_prefix[-1] in _STREET_BAD_TAILS:
        return False
    if len(non_prefix) == 1 and non_prefix[0] in _FALSE_STREET_WORDS:
        return False
    return True


def detect_addresses_plz_housenumbers(
    text: str,
    want_address: bool,
    want_house_number: bool,
    want_plz: bool,
    *,
    text_has_digit: bool | None = None,
) -> list[SpanMatch]:
    has_num = has_digit(text) if text_has_digit is None else text_has_digit
    if not has_num and ":" not in text and "=" not in text:
        return []

    matches: list[SpanMatch] = []

    if want_address:
        if has_num:
            for m in STREET_COMPOUND_RE.finditer(text):
                raw = m.group(0).strip()
                cleaned = _LEADING_PREP_STRIP_RE.sub("", raw)
                offset = len(raw) - len(cleaned)
                if is_plausible_street(cleaned):
                    matches.append(SpanMatch(m.start() + offset, m.start() + offset + len(cleaned), cleaned, Category.ADDRESS, Confidence.HIGH, "Straße + Hausnummer"))

            for m in STREET_PREFIX_RE.finditer(text):
                raw = m.group(0).strip()
                if is_plausible_street(raw):
                    matches.append(SpanMatch(m.start(), m.start() + len(raw), raw, Category.ADDRESS, Confidence.HIGH, "Straße + Hausnummer (Präfix)"))

        if ":" in text or "=" in text:
            for m in _ADDRESS_LABEL_RE.finditer(text):
                val = m.group(1).strip()
                if val and val.lower() not in NON_NAME_WORDS:
                    matches.append(SpanMatch(m.start(1), m.start(1) + len(val), val, Category.ADDRESS, Confidence.HIGH, "Adresse (Kontext-Feld)"))

    if want_house_number and has_num:
        for regex, rule in (
            (_HOUSENUMBER_EXPLICIT_RE, "Hausnummer (explizit)"),
            (_HOUSENUMBER_NR_CONTEXT_RE, "Hausnummer (Adresskontext)"),
        ):
            for m in regex.finditer(text):
                val = m.group(1).strip()
                matches.append(SpanMatch(m.start(1), m.start(1) + len(val), val, Category.HOUSE_NUMBER, Confidence.HIGH, rule))

    if want_plz and has_num:
        matched_plz_ranges: list[tuple[int, int]] = []

        for m in _PLZ_EXPLICIT_RE.finditer(text):
            plz_val = m.group(1)
            is_valid = (len(plz_val) == 5 and validate_german_plz(plz_val)) or (
                len(plz_val) == 4 and 1000 <= int(plz_val) <= 9999
            )
            if is_valid:
                matches.append(SpanMatch(m.start(1), m.end(1), plz_val, Category.POSTAL_CODE, Confidence.HIGH, "PLZ (explizit)"))
                matched_plz_ranges.append((m.start(1), m.end(1)))

        for m in _PLZ_COUNTRY_CITY_RE.finditer(text):
            if validate_german_plz(m.group(1)):
                cat = Category.ADDRESS if (m.group(2) and want_address) else Category.POSTAL_CODE
                matches.append(SpanMatch(m.start(), m.end(), m.group(0), cat, Confidence.HIGH, "PLZ + Ort (Länderpräfix DE)"))
                matched_plz_ranges.append((m.start(), m.end()))

        for m in _PLZ_AT_CH_COUNTRY_CITY_RE.finditer(text):
            cat = Category.ADDRESS if (m.group(2) and want_address) else Category.POSTAL_CODE
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), cat, Confidence.HIGH, "PLZ + Ort (AT/CH)"))
            matched_plz_ranges.append((m.start(), m.end()))

        for m in _PLZ_CITY_RE.finditer(text):
            plz, city = m.group(1), m.group(2)
            if not validate_german_plz(plz):
                continue
            if any(s <= m.start(1) and m.end(1) <= e for s, e in matched_plz_ranges):
                continue
            before = text[max(0, m.start(1) - 30):m.start(1)]
            if _NON_PLZ_BEFORE_RE.search(before):
                continue
            city_first = city.split()[0].lower()
            if city_first in _NOT_A_CITY_AFTER_PLZ:
                continue
            if city.lower() in MAJOR_GERMAN_CITIES or city_first not in NON_NAME_WORDS:
                span_end = m.end(2) if want_address else m.end(1)
                cat = Category.ADDRESS if want_address else Category.POSTAL_CODE
                matches.append(SpanMatch(m.start(1), span_end, text[m.start(1):span_end], cat, Confidence.HIGH, "PLZ + Ort"))
                matched_plz_ranges.append((m.start(1), span_end))

        for m in _PLZ_STANDALONE_RE.finditer(text):
            plz = m.group(1)
            if not validate_german_plz(plz):
                continue
            if any(s <= m.start(1) and m.end(1) <= e for s, e in matched_plz_ranges):
                continue
            if _NON_PLZ_BEFORE_RE.search(text[max(0, m.start(1) - 35):m.start(1)]):
                continue
            if _PLZ_UNIT_AFTER_RE.match(text[m.end(1):min(len(text), m.end(1) + 20)]):
                continue
            matches.append(SpanMatch(m.start(1), m.end(1), plz, Category.POSTAL_CODE, Confidence.REVIEW, "Mögliche PLZ (5-stellig, bitte prüfen)"))

    return matches


# --- Ortschaften / Städte (Behörden wie „Stadt Köln“ bleiben sichtbar!) ---

_PUBLIC_AUTH_BEFORE_LOC_RE = re.compile(
    r"(?i)\b(?:Stadt|Gemeinde|Kreis|Landkreis|Bezirksamt|Ordnungsamt|Bürgeramt|"
    r"Straßenverkehrsamt|Strassenverkehrsamt|Tiefbauamt|Bauamt|Finanzamt|Polizei|"
    r"Polizeipräsidium|Landratsamt|Stadtverwaltung|Gemeindeverwaltung|Kreisverwaltung|"
    r"Bezirksregierung|Senatsverwaltung|Magistrat|Rathaus|Landesamt|Bundesamt|"
    r"Amt[ \t]+für[ \t]+\w+)(?:[ \t]+(?:der[ \t]+|des[ \t]+)?)?[ \t]+\Z"
)
_HONORIFIC_BEFORE_LOC_RE = re.compile(r"(?i)\b(?:Herrn?|Frau|Hr\.?|Fr\.?|Prof\.?|Dr\.?)[ \t]+\Z")
_LOC_PREP_BEFORE_RE = re.compile(
    r"(?i)(?:\b(?:in|aus|nach|bei|von|um|Richtung|Raum|Bereich|Umgebung|Ort|Wohnort|Ortschaft|Gemeinde|Stadtteil)[ \t]*[:=]?[ \t]+|,[ \t]+)\Z"
)
_LEGAL_FORM_AFTER_LOC_RE = re.compile(
    r"^[ \t]+(?:(?:&\s*Co\.?[ \t]*)?(?:GmbH|UG|AG|OHG|KG|GbR|e\.?\s*K\.?|e\.?\s*V\.?|SE|Ltd\.?|Inc\.?|mbH|PartG|KGaA))\b"
)
_TOWN_TOKEN = r"[A-ZÄÖÜ][a-zäöüßéèê]+(?:-[A-ZÄÖÜ][a-zäöüßéèê]+)*"
_TOWN_PHRASE = (
    rf"(?:(?:Bad|Sankt|St\.|Bergisch|Mönchengladbach|Neu|Alt|Ober|Unter|Groß|Klein)[ \t]+)?{_TOWN_TOKEN}"
    rf"(?:[ \t]+(?:am|an|im|in|ob|vor|bei)[ \t]+(?:der[ \t]+|dem[ \t]+|den[ \t]+)?{_TOWN_TOKEN})?"
)
_LOCATION_LABEL_RE = re.compile(
    rf"(?i)\b(?:Wohnort|Ortschaft|Ort|Stadtteil|Ortsteil|Abholort|Zielort|Lieferort)[ \t]*[:=][ \t]*({_TOWN_PHRASE})"
)
_UNAMBIGUOUS_CITY_PAT = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(MAJOR_GERMAN_CITIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _is_protected_authority_context(text: str, start: int) -> bool:
    line_before = text[max(0, start - 40):start].rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
    return bool(_PUBLIC_AUTH_BEFORE_LOC_RE.search(line_before))


def detect_locations(text: str) -> list[SpanMatch]:
    """Erkennt Ortschaften / Städte / Wohnorte, lässt aber Behörden wie 'Stadt Köln' sichtbar."""
    matches: list[SpanMatch] = []

    if ":" in text or "=" in text:
        for m in _LOCATION_LABEL_RE.finditer(text):
            val = m.group(1).strip(" .,:;")
            low = val.lower()
            if not val or low in COUNTRIES_AND_REGIONS_NON_TOWN:
                continue
            if low in NON_NAME_WORDS and low not in MAJOR_GERMAN_CITIES:
                continue
            if _is_protected_authority_context(text, m.start(1)):
                continue
            matches.append(SpanMatch(m.start(1), m.start(1) + len(val), val, Category.LOCATION, Confidence.HIGH, "Ortschaft / Wohnort (Kontext-Feld)"))

    for m in _UNAMBIGUOUS_CITY_PAT.finditer(text):
        raw = m.group(1)
        if not raw[0].isupper():
            continue
        start, end = m.start(1), m.end(1)
        low = raw.lower()
        if low in COUNTRIES_AND_REGIONS_NON_TOWN or _is_protected_authority_context(text, start):
            continue
        line_before = text[max(0, start - 30):start].rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]
        if _HONORIFIC_BEFORE_LOC_RE.search(line_before) or _LEGAL_FORM_AFTER_LOC_RE.match(text[end:]):
            continue
        if low in AMBIGUOUS_CITY_WORDS and not _LOC_PREP_BEFORE_RE.search(line_before):
            continue
        matches.append(SpanMatch(start, end, raw, Category.LOCATION, Confidence.HIGH, "Ortschaft / Stadt (Lexikon)"))

    return matches
