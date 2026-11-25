"""Strukturierte PII-Detektoren (Kontodaten, E-Mail, URL, IP, Telefon, Beträge, Geburtsdatum, KFZ, IDs, JSON)."""

from __future__ import annotations

import re
from functools import lru_cache

from .detectors_common import has_digit
from .lexicons import NON_NAME_WORDS
from .models import Category, Confidence, SpanMatch
from .validators import (
    validate_german_kfz,
    validate_german_tax_id,
    validate_iban,
    validate_luhn,
)

# --- 1. Kontodaten (IBAN, BIC, Kontonummer, BLZ, Kreditkarte) ---

_IBAN_CONTEXT_RE = re.compile(
    r"(?i)\bIBAN[ \t]*[:=]?[ \t]*([A-Z]{2}[ \t]*\d{2}[ \t\-]?[A-Z0-9][ \t\-A-Z0-9]{10,36})"
)
_IBAN_STANDALONE_RE = re.compile(
    r"\b([A-Z]{2}\d{2}(?:[ \t\-]?[A-Z0-9]{4}){2,7}(?:[ \t\-]?[A-Z0-9]{1,4})?)\b"
)
_BIC_CONTEXT_RE = re.compile(
    r"(?i)\b(?:BIC|SWIFT(?:-?Code)?)[ \t]*[:=]?[ \t]*([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b"
)
_BIC_STANDALONE_RE = re.compile(
    r"\b([A-Z]{4}(?:DE|AT|CH|NL|FR|BE|LU|IT|ES|GB|PL|US|DK|SE|NO|FI|IE|PT|CZ)[A-Z2-9][A-NP-Z0-9](?:[A-Z0-9]{3})?)\b"
)
_KONTO_BLZ_RE = re.compile(
    r"(?i)\b(?:Konto(?:nummer|-?Nr\.?)|BLZ|Bankleitzahl)[ \t]*[:=]?[ \t]*(\d[\d \t\-]{4,18}\d)\b"
)
_BANK_NAME_RE = re.compile(
    r"(?i)\b(?:Bank(?:name)?|Kreditinstitut|Geldinstitut)[ \t]*[:=][ \t]*([^\n,;]{3,55})"
)
_CARD_RE = re.compile(r"(?<!\d)(\d{4}[ \t\-]?\d{4}[ \t\-]?\d{4}[ \t\-]?\d{1,7})(?!\d)")


def detect_bank(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    matches: list[SpanMatch] = []
    has_num = has_digit(text) if text_has_digit is None else text_has_digit

    if has_num:
        for m in _IBAN_CONTEXT_RE.finditer(text):
            raw = m.group(1).strip(" .,:;")
            start = m.start(1)
            valid = validate_iban(raw)
            rule = "IBAN (ISO 13616 geprüft + Kontext)" if valid else "IBAN (Kontext-Treffer)"
            matches.append(SpanMatch(start, start + len(raw), text[start:start + len(raw)], Category.BANK, Confidence.HIGH, rule))

        for m in _IBAN_STANDALONE_RE.finditer(text):
            raw = m.group(1).strip()
            if validate_iban(raw):
                matches.append(SpanMatch(m.start(1), m.start(1) + len(raw), raw, Category.BANK, Confidence.HIGH, "IBAN (ISO 13616 Mod-97 gültig)"))

        for m in _KONTO_BLZ_RE.finditer(text):
            val = m.group(1).strip()
            matches.append(SpanMatch(m.start(1), m.start(1) + len(val), val, Category.BANK, Confidence.HIGH, "Kontonummer / BLZ"))

        for m in _CARD_RE.finditer(text):
            raw = m.group(1)
            if validate_luhn(raw):
                matches.append(SpanMatch(m.start(1), m.end(1), raw, Category.BANK, Confidence.HIGH, "Kreditkarte (Luhn)"))

    bic_ranges: list[tuple[int, int]] = []
    for m in _BIC_CONTEXT_RE.finditer(text):
        matches.append(SpanMatch(m.start(1), m.end(1), m.group(1), Category.BANK, Confidence.HIGH, "BIC/SWIFT"))
        bic_ranges.append((m.start(1), m.end(1)))

    for m in _BIC_STANDALONE_RE.finditer(text):
        val = m.group(1)
        if any(s <= m.start(1) and m.end(1) <= e for s, e in bic_ranges):
            continue
        has_strong_cue = has_digit(val[6:]) or val.endswith("XXX") or len(val) == 11
        if not has_strong_cue and val.isalpha() and len(val) == 8:
            if val.lower() in NON_NAME_WORDS:
                continue
            conf = Confidence.REVIEW
        else:
            conf = Confidence.HIGH
        matches.append(SpanMatch(m.start(1), m.end(1), val, Category.BANK, conf, "BIC/SWIFT (freistehend)"))

    if ":" in text or "=" in text:
        for m in _BANK_NAME_RE.finditer(text):
            val = m.group(1).strip()
            if val and val.lower() not in NON_NAME_WORDS:
                matches.append(SpanMatch(m.start(1), m.start(1) + len(val), val, Category.BANK, Confidence.HIGH, "Bankname (Kontext)"))

    return matches


# --- 2. E-Mail-Adressen & Webseiten ---

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+(?:\s*@\s*|\s*\[at\]\s*|\s*\(at\)\s*)[A-Za-z0-9.\-]+\.[A-Za-z]{2,15}\b"
)
URL_FULL_RE = re.compile(
    r"\b(?:https?://|www\.)[^\s<>\"'()\[\]{},;]+[^\s<>\"'()\[\]{},;.:!?]",
    re.IGNORECASE,
)
BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w/\.])([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\."
    r"(?:de|com|net|org|eu|at|ch|info|io|co|biz|nrw|koeln|berlin|bayern|hamburg|gmbh|online|shop|app))"
    r"(?:/[^\s<>\"'()\[\]{},;]*)?\b",
    re.IGNORECASE,
)
_FILE_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|bmp|svg|webp|pdf|docx?|xlsx?|pptx?|csv|txt|zip|rar|7z|eml|msg|mp4|mov|mp3)$",
    re.IGNORECASE,
)


def detect_emails(text: str) -> list[SpanMatch]:
    if "@" not in text and "[at]" not in text and "(at)" not in text:
        return []
    return [
        SpanMatch(m.start(), m.end(), m.group(0), Category.EMAIL, Confidence.HIGH, "E-Mail-Adresse")
        for m in EMAIL_RE.finditer(text)
    ]


def detect_websites(text: str, email_spans: list[tuple[int, int]]) -> list[SpanMatch]:
    if "." not in text:
        return []
    matches: list[SpanMatch] = []

    def inside_email(s: int, e: int) -> bool:
        return any(es <= s and e <= ee for es, ee in email_spans)

    for m in URL_FULL_RE.finditer(text):
        if not inside_email(m.start(), m.end()):
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.WEBSITE, Confidence.HIGH, "URL / Webseite"))

    for m in BARE_DOMAIN_RE.finditer(text):
        raw = m.group(0)
        if inside_email(m.start(), m.end()) or _FILE_EXT_RE.search(raw):
            continue
        if any(m.start() >= prev.start and m.end() <= prev.end for prev in matches):
            continue
        matches.append(SpanMatch(m.start(), m.end(), raw, Category.WEBSITE, Confidence.HIGH, "Domain / Webseite"))

    return matches


# --- 2b. IP-Adressen (IPv4) ---

_IPV4_RE = re.compile(r"(?<![A-Za-z0-9.\-/])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
_VERSION_PREFIX_RE = re.compile(r"(?i)\b(?:v|ver\.?|version|release|build|app|ios|macos|chrome|safari)[ \t]*\Z")


def detect_ip_addresses(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if "." not in text or not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    matches: list[SpanMatch] = []
    for m in _IPV4_RE.finditer(text):
        raw = m.group(1)
        parts = raw.split(".")
        if not all(0 <= int(p) <= 255 for p in parts):
            continue
        if any(len(p) > 1 and p[0] == "0" for p in parts):
            continue
        before = text[max(0, m.start(1) - 14):m.start(1)]
        if _VERSION_PREFIX_RE.search(before):
            continue
        matches.append(SpanMatch(m.start(1), m.end(1), raw, Category.IP_ADDRESS, Confidence.HIGH, "IPv4-Adresse"))
    return matches


# --- 3. Telefonnummern ---

_PHONE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:(?:Tel(?:efon)?\.?|Mobil(?:funk)?|Handy|Fax|Fon|Durchwahl|Rufnummer|Rückruf(?:nummer)?|"
    r"Telefonnummer|Tel\.-?Nr\.?|WhatsApp)[ \t]*[:.]?[ \t]*|(?:T|M|F)[ \t]*[:.][ \t]*)"
    r"((?:\+|00)?\d[\d \t/\-\(\)\.]{5,24}\d)"
)
_PHONE_INTL_RE = re.compile(
    r"(?<!\d)((?:\+|00)(?:49|43|41|31|32|33|44|45|48|39|34|1)"
    r"(?:[ \t]*[/\-][ \t]*|[ \t]+)?(?:\(0\)(?:[ \t]*[/\-][ \t]*|[ \t]+)?)?"
    r"\d[\d \t/\-\(\)]{5,20}\d)(?!\d)"
)
_PHONE_DE_MOBILE_RE = re.compile(
    r"(?<!\d)(0(?:15\d|16\d|17\d)(?:[ \t]*[/\-][ \t]*|[ \t]+)?\d{2,4}"
    r"(?:[ \t]*[/\-][ \t]*|[ \t]+)?\d{2,8}(?:[ \t\-]\d{1,5})?)(?!\d)"
)
_PHONE_DE_LANDLINE_RE = re.compile(
    r"(?<!\d)("
    r"\(0\d{2,5}\)[ \t]*\d{2,9}(?:[ \t\-]\d{1,6}){0,3}"
    r"|0[2-9]\d{1,4}(?:[ \t]*[/\-][ \t]*|[ \t]+)\d{2,9}(?:[ \t\-]\d{1,6}){0,3}"
    r")(?!\d)"
)
_DATE_LIKE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")
_ORDER_PREFIX_RE = re.compile(r"[A-Za-z]\d*[-]?\Z")


def _looks_like_valid_phone(raw: str) -> bool:
    if _DATE_LIKE_RE.match(raw):
        return False
    digit_count = sum(1 for c in raw if "0" <= c <= "9")
    return 7 <= digit_count <= 16


def detect_phones(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    matches: list[SpanMatch] = []

    for m in _PHONE_CONTEXT_RE.finditer(text):
        raw = m.group(1).strip()
        if _looks_like_valid_phone(raw):
            matches.append(SpanMatch(m.start(1), m.start(1) + len(raw), raw, Category.PHONE, Confidence.HIGH, "Telefonnummer (Kontext)"))

    for regex, label in (
        (_PHONE_INTL_RE, "Telefonnummer (International)"),
        (_PHONE_DE_MOBILE_RE, "Mobilnummer (DE)"),
        (_PHONE_DE_LANDLINE_RE, "Festnetznummer (DE)"),
    ):
        for m in regex.finditer(text):
            raw = m.group(1).strip()
            if _looks_like_valid_phone(raw):
                line_before = (
                    text[max(0, m.start(1) - 12):m.start(1)]
                    .rsplit("\n", 1)[-1]
                    .rsplit("\r", 1)[-1]
                )
                if _ORDER_PREFIX_RE.search(line_before):
                    continue
                matches.append(SpanMatch(m.start(1), m.start(1) + len(raw), raw, Category.PHONE, Confidence.HIGH, label))

    return matches


# --- 4. Geldbeträge ---

_NUM_AMOUNT = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2}|,-|,--)?|\d+(?:,\d{1,2}|,-|,--|\.\d{2})?)"
_AMOUNT_AFTER_CURR_RE = re.compile(
    rf"(?:€|EUR|Euro|CHF|USD|\$|GBP|£)\s*({_NUM_AMOUNT})(?![\d.,a-zA-Zäöüß])",
    re.IGNORECASE,
)
_AMOUNT_BEFORE_CURR_RE = re.compile(
    rf"(?<![\d.,A-Za-z])({_NUM_AMOUNT})\s*(?:€|EUR\b|Euro\b|CHF\b|USD\b|\$|GBP\b|£)",
    re.IGNORECASE,
)
_AMOUNT_CONTEXT_RE = re.compile(
    r"(?i)\b(?:(?:Gesamt|End|Rechnungs|Erstattungs|Rückerstattungs|Netto|Brutto|Teil|Rest|Kauf|Miet|Monats|Jahres|Stunden)?)"
    r"(?:betrag|summe|preis|kosten|gebühr(?:en)?|rückerstattung|erstattung|gutschrift|anzahlung|"
    r"zahlung|überweisung|gehalt|lohn|honorar|vergütung|guthaben|saldo|kaution|rabatt|nachlass|skonto|"
    r"in\s+Höhe\s+von|i\.?\s*H\.?\s*v\.?)"
    rf"\s*(?:[:=]|von|über|beträgt|i\.?\s*H\.?\s*v\.?|in\s+Höhe\s+von)?\s*(?:ca\.?\s*|rund\s*|etwa\s*)?"
    rf"({_NUM_AMOUNT}(?:\s*(?:€|EUR|Euro|CHF|USD|\$))?)"
    r"(?!\s*(?:t\b|to\b|Tonnen?\b|kg\b|g\b|m\b|cm\b|mm\b|km\b|Meter\b|Tage?\b|Wochen?\b|Monate?\b|Jahre?\b|Std\.?\b|Stunden?\b|Minuten?\b|Uhr\b|%|Prozent\b|x\b|Stück\b|Schilder\b|LKW\b|PKW\b))",
)
_DATE_CONTINUATION_RE = re.compile(r"^\.\d{2,4}")
_YEAR_ONLY_RE = re.compile(r"^(?:19\d\d|20[0-3]\d)$")


def detect_amounts(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    matches: list[SpanMatch] = []

    for regex in (_AMOUNT_AFTER_CURR_RE, _AMOUNT_BEFORE_CURR_RE):
        for m in regex.finditer(text):
            if _DATE_CONTINUATION_RE.match(text[m.end():]):
                continue
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.AMOUNT, Confidence.HIGH, "Geldbetrag (Währung)"))

    for m in _AMOUNT_CONTEXT_RE.finditer(text):
        val = m.group(1).strip()
        start = m.start(1)
        end = start + len(val)
        if not val or _DATE_CONTINUATION_RE.match(text[end:]) or _YEAR_ONLY_RE.match(val):
            continue
        matches.append(SpanMatch(start, end, val, Category.AMOUNT, Confidence.HIGH, "Geldbetrag (Kontext)"))

    return matches


# --- 5. Geburtsdaten, Kfz-Kennzeichen, Steuer-/Ausweis-IDs ---

_GERMAN_MONTHS_ALT = (
    r"Januar|Jan\.?|Februar|Feb\.?|März|Maerz|Mrz\.?|April|Apr\.?|Mai|Juni|Jun\.?|"
    r"Juli|Jul\.?|August|Aug\.?|September|Sept?\.?|Oktober|Okt\.?|November|Nov\.?|Dezember|Dez\.?"
)
_BIRTHDATE_RE = re.compile(
    r"(?i)(?:\b(?:geb\.?|geboren(?:\s+am)?|Geburtsdatum|Geb\.?-?Dat\.?|Geburtstag|DOB)\s*[:.]?\s*|\*\s*)"
    rf"(\d{{1,2}}[./\-]\d{{1,2}}[./\-](?:19|20)?\d{{2}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\.\s*(?:{_GERMAN_MONTHS_ALT})\s+(?:19|20)\d{{2}})"
)

_KFZ_CONTEXT_RE = re.compile(
    r"(?i)\b(?:Kennzeichen|Kfz(?:-?Kennzeichen)?|Amtl\.?\s*Kennzeichen|Nummernschild|"
    r"Fahrzeug(?:kennzeichen)?|LKW|PKW|Transporter|Sprinter)\s*[:=]?\s*"
    r"([A-ZÄÖÜ]{1,3})[\s\-]([A-Z]{1,2})[\s\-]?([1-9]\d{0,3})([EH])?\b"
)
_KFZ_HYPHEN_RE = re.compile(
    r"(?<![A-ZÄÖÜa-zäöüß0-9\-])([A-ZÄÖÜ]{1,3})-([A-Z]{1,2})[\s\-]([1-9]\d{0,3})([EH])?(?![A-ZÄÖÜa-zäöüß0-9\-])"
)
_KFZ_SPACE_RE = re.compile(
    r"(?<![A-ZÄÖÜa-zäöüß0-9\-])([A-ZÄÖÜ]{1,3})[ \t]+([A-Z]{1,2})[ \t]+([1-9]\d{0,3})([EH])?(?![A-ZÄÖÜa-zäöüß0-9\-])"
)
_KFZ_SPACE_FALSE_PREFIXES = frozenset({"AM", "AN", "IM", "IN", "ZU", "UM", "AB", "OB", "ER", "ES", "DA", "SO", "WO", "NR", "NO", "ID"})

_UST_ID_RE = re.compile(r"\b(DE\s?\d{9})\b")
_TAX_CONTEXT_RE = re.compile(
    r"(?i)\b(?:Steuer-?ID|Steuer-?Identifikationsnummer|Steuernummer|St\.?-?Nr\.?|IdNr\.?|"
    r"USt\.?-?IdNr\.?|Umsatzsteuer-?ID|Sozialversicherungsnummer|SV-?Nr\.?|RVNR|"
    r"Personalausweis(?:nummer|-?Nr\.?)?|Ausweisnummer|Reisepass(?:nummer|-?Nr\.?)?)\s*[:=]?\s*"
    r"([A-Z0-9][A-Z0-9\s/\-]{6,22}[A-Z0-9])"
)
_TAX_ID_11_RE = re.compile(r"(?<!\d)(\d{2}\s?\d{3}\s?\d{3}\s?\d{3})(?!\d)")
_RVNR_RE = re.compile(r"\b(\d{2}\s?\d{6}\s?[A-Z]\s?\d{3})\b")


def detect_birthdates(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    return [
        SpanMatch(m.start(1), m.end(1), m.group(1), Category.BIRTHDATE, Confidence.HIGH, "Geburtsdatum")
        for m in _BIRTHDATE_RE.finditer(text)
    ]


def detect_license_plates(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    matches: list[SpanMatch] = []
    for m in _KFZ_CONTEXT_RE.finditer(text):
        if validate_german_kfz(m.group(1), m.group(2), m.group(3)):
            end = m.end(4) if m.group(4) else m.end(3)
            matches.append(SpanMatch(m.start(1), end, text[m.start(1):end], Category.LICENSE_PLATE, Confidence.HIGH, "Kfz-Kennzeichen (Kontext)"))
    for m in _KFZ_HYPHEN_RE.finditer(text):
        if validate_german_kfz(m.group(1), m.group(2), m.group(3)):
            end = m.end(4) if m.group(4) else m.end(3)
            matches.append(SpanMatch(m.start(1), end, text[m.start(1):end], Category.LICENSE_PLATE, Confidence.HIGH, "Kfz-Kennzeichen (DE)"))
    for m in _KFZ_SPACE_RE.finditer(text):
        district, letters, digits = m.group(1), m.group(2), m.group(3)
        if district in _KFZ_SPACE_FALSE_PREFIXES:
            continue
        if len(digits) == 4 and 1990 <= int(digits) <= 2035:
            continue
        if validate_german_kfz(district, letters, digits):
            end = m.end(4) if m.group(4) else m.end(3)
            matches.append(SpanMatch(m.start(1), end, text[m.start(1):end], Category.LICENSE_PLATE, Confidence.REVIEW, "Mögliches Kfz-Kennzeichen (ohne Bindestrich)"))
    return matches


def detect_tax_ids(text: str, *, text_has_digit: bool | None = None) -> list[SpanMatch]:
    if not (has_digit(text) if text_has_digit is None else text_has_digit):
        return []
    matches: list[SpanMatch] = []
    context_ranges: list[tuple[int, int]] = []
    for m in _TAX_CONTEXT_RE.finditer(text):
        val = m.group(1).strip(" .,:;")
        if sum(1 for c in val if not c.isspace()) >= 7:
            s, e = m.start(1), m.start(1) + len(val)
            matches.append(SpanMatch(s, e, val, Category.TAX_ID, Confidence.HIGH, "Steuer-/Sozialversicherungs-/Ausweis-ID (Kontext)"))
            context_ranges.append((s, e))

    for m in _UST_ID_RE.finditer(text):
        in_ctx = any(s <= m.start(1) and m.end(1) <= e for s, e in context_ranges)
        conf = Confidence.HIGH if in_ctx else Confidence.REVIEW
        rule = "USt-IdNr (DE)" if in_ctx else "Mögliche USt-IdNr (DE, ohne Label)"
        matches.append(SpanMatch(m.start(1), m.end(1), m.group(1), Category.TAX_ID, conf, rule))

    for m in _TAX_ID_11_RE.finditer(text):
        raw = m.group(1)
        if validate_german_tax_id(raw):
            matches.append(SpanMatch(m.start(1), m.end(1), raw, Category.TAX_ID, Confidence.HIGH, "Deutsche Steuer-ID (Prüfziffer gültig)"))

    for m in _RVNR_RE.finditer(text):
        matches.append(SpanMatch(m.start(1), m.end(1), m.group(1), Category.TAX_ID, Confidence.HIGH, "Sozialversicherungsnummer (RVNR)"))

    return matches


# --- 6. Strukturierte JSON-/Query-Felder ---

_JSON_KV_PII_MAP: dict[str, Category] = {
    "strasse": Category.ADDRESS, "straße": Category.ADDRESS, "strasse1": Category.ADDRESS,
    "strasse2": Category.ADDRESS, "straße1": Category.ADDRESS, "straße2": Category.ADDRESS,
    "strasse_1": Category.ADDRESS, "strasse_2": Category.ADDRESS, "street": Category.ADDRESS,
    "street1": Category.ADDRESS, "street2": Category.ADDRESS, "adresse": Category.ADDRESS,
    "address": Category.ADDRESS, "anschrift": Category.ADDRESS, "wohnadresse": Category.ADDRESS,
    "lieferadresse": Category.ADDRESS, "rechnungsadresse": Category.ADDRESS,
    "hausnummer": Category.HOUSE_NUMBER, "hausnr": Category.HOUSE_NUMBER, "house_number": Category.HOUSE_NUMBER,
    "plz": Category.POSTAL_CODE, "postleitzahl": Category.POSTAL_CODE, "zip": Category.POSTAL_CODE,
    "zipcode": Category.POSTAL_CODE, "zip_code": Category.POSTAL_CODE, "postal_code": Category.POSTAL_CODE,
    "ort": Category.LOCATION, "stadt": Category.LOCATION, "city": Category.LOCATION,
    "wohnort": Category.LOCATION, "bezirk": Category.LOCATION, "unterort": Category.LOCATION,
    "ortsteil": Category.LOCATION, "stadtteil": Category.LOCATION,
    "name": Category.NAME, "vorname": Category.NAME, "nachname": Category.NAME,
    "first_name": Category.NAME, "firstname": Category.NAME, "last_name": Category.NAME,
    "lastname": Category.NAME, "full_name": Category.NAME, "fullname": Category.NAME,
    "kundenname": Category.NAME, "ansprechpartner": Category.NAME, "kontoinhaber": Category.NAME,
    "empfaenger": Category.NAME, "empfänger": Category.NAME, "absender": Category.NAME,
    "email": Category.EMAIL, "e_mail": Category.EMAIL, "e-mail": Category.EMAIL,
    "mail": Category.EMAIL, "bcc_mail": Category.EMAIL, "cc_mail": Category.EMAIL,
    "from_mail": Category.EMAIL, "to_mail": Category.EMAIL, "kunden_email": Category.EMAIL,
    "kunden_mail": Category.EMAIL,
    "telefon": Category.PHONE, "telefonnummer": Category.PHONE, "tel": Category.PHONE,
    "phone": Category.PHONE, "mobile": Category.PHONE, "mobil": Category.PHONE,
    "handy": Category.PHONE, "fax": Category.PHONE, "rufnummer": Category.PHONE,
    "iban": Category.BANK, "bic": Category.BANK, "swift": Category.BANK,
    "kontonummer": Category.BANK, "blz": Category.BANK, "bank": Category.BANK, "bankname": Category.BANK,
    "total_preis": Category.AMOUNT, "gesamtpreis": Category.AMOUNT, "gesamtbetrag": Category.AMOUNT,
    "preis": Category.AMOUNT, "betrag": Category.AMOUNT, "price": Category.AMOUNT, "amount": Category.AMOUNT,
    "geburtsdatum": Category.BIRTHDATE, "geb_datum": Category.BIRTHDATE, "birthdate": Category.BIRTHDATE, "dob": Category.BIRTHDATE,
    "kennzeichen": Category.LICENSE_PLATE, "kfz": Category.LICENSE_PLATE, "license_plate": Category.LICENSE_PLATE,
    "ip": Category.IP_ADDRESS, "ip_address": Category.IP_ADDRESS, "ip_adresse": Category.IP_ADDRESS,
    "firma": Category.COMPANY, "firmenname": Category.COMPANY, "company": Category.COMPANY, "unternehmen": Category.COMPANY,
}

_JSON_KV_RE = re.compile(
    r'(?<=[{,])\s*"([A-Za-z0-9_\-äöüÄÖÜß]{2,40})"\s*:\s*(?:"((?:[^"\\]|\\.)+)"|(\d+(?:[.,]\d+)?))'
)
_JSON_EMPTY_VALS = frozenset({"null", "none", "n/a", "-", "—", "true", "false", "ja", "nein"})
_ALREADY_REDACTED_VAL_RE = re.compile(r"^(?:[█\s]+|\[[A-ZÄÖÜ0-9_ -]+\])$")


def detect_json_kv_fields(text: str, enabled: set[Category]) -> list[SpanMatch]:
    """Erkennt PII-Werte in eingebetteten JSON-/Query-Objekten (z. B. {"strasse":"Kufsteiner 43"})."""
    if '":' not in text and '" :' not in text:
        return []
    matches: list[SpanMatch] = []
    for m in _JSON_KV_RE.finditer(text):
        key_l = m.group(1).strip().lower()
        cat = _JSON_KV_PII_MAP.get(key_l)
        if cat is None or cat not in enabled:
            continue
        is_quoted = m.group(2) is not None
        val_raw = m.group(2) if is_quoted else m.group(3)
        if not val_raw:
            continue
        val_clean = val_raw.strip()
        if not val_clean or val_clean.lower() in _JSON_EMPTY_VALS or _ALREADY_REDACTED_VAL_RE.match(val_clean):
            continue
        if not is_quoted and cat not in (Category.POSTAL_CODE, Category.AMOUNT, Category.HOUSE_NUMBER):
            continue
        if cat == Category.AMOUNT and val_clean in {"0", "0.0", "0.00", "0,00"}:
            continue
        grp = 2 if is_quoted else 3
        start, end = m.start(grp), m.end(grp)
        matches.append(SpanMatch(start, end, text[start:end], cat, Confidence.HIGH, f'JSON-Feld "{m.group(1)}"'))
    return matches


# --- 7. Benutzerdefinierte Blacklist (Regex gecacht pro Begriff!) ---

_WORD_EDGE_RE = re.compile(r"[\wÄÖÜäöüß]")


@lru_cache(maxsize=512)
def _compile_blacklist_pattern(clean: str) -> re.Pattern[str]:
    esc = re.escape(clean)
    prefix = r"\b" if _WORD_EDGE_RE.match(clean[0]) else ""
    suffix = r"\b" if _WORD_EDGE_RE.match(clean[-1]) else ""
    return re.compile(rf"{prefix}{esc}{suffix}", re.IGNORECASE)


def detect_custom_blacklist(text: str, terms: list[str]) -> list[SpanMatch]:
    matches: list[SpanMatch] = []
    text_l = text.lower()
    for term in terms:
        clean = term.strip()
        if not clean or clean.lower() not in text_l:
            continue
        pat = _compile_blacklist_pattern(clean)
        for m in pat.finditer(text):
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.MANUAL, Confidence.HIGH, f"Eigene Sperrliste ({clean})"))
    return matches
