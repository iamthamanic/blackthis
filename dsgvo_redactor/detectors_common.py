"""Gemeinsame, vorkompilierte Bausteine & Fast-Path-Helfer für alle Detektor-Module."""

from __future__ import annotations

import re
from functools import lru_cache

from .lexicons import (
    COMMON_FIRST_NAMES,
    COMMON_SURNAMES,
    GERMAN_COMPOUND_NOUN_SUFFIXES,
    MAJOR_GERMAN_CITIES,
    PUBLIC_AUTHORITY_PREFIXES,
)

NAME_LOWER_CHARS = r"a-zäöüßéèêáàâóòôíìîúùûñçğşıćńśźżłęąćšžřěůý"
NAME_UPPER_CHARS = r"A-ZÄÖÜÉÈÊÁÀÂÓÒÔÍÌÎÚÙÛÑÇĞŞİĆŃŚŹŻŁČŠŽ"
NAME_TOKEN = (
    rf"(?:(?:Mc|Mac|O'|Al-|El-)[{NAME_UPPER_CHARS}][{NAME_LOWER_CHARS}]+|"
    rf"[{NAME_UPPER_CHARS}][{NAME_LOWER_CHARS}]+)"
    rf"(?:-[{NAME_UPPER_CHARS}][{NAME_LOWER_CHARS}]+)?"
)
NAME_TOKEN_CI = rf"[{NAME_UPPER_CHARS}{NAME_LOWER_CHARS}]{{2,}}(?:-[{NAME_UPPER_CHARS}{NAME_LOWER_CHARS}]{{2,}})?"
NAME_PARTICLE = r"(?:von(?:[ \t]+der|[ \t]+dem|[ \t]+den)?|van(?:[ \t]+der|[ \t]+den)?|de(?:[ \t]+la)?|zu|zum|zur|del|di|da|al|el|ben)"
NAME_SEQ = rf"(?:[{NAME_UPPER_CHARS}]\.[ \t]+)?{NAME_TOKEN}(?:[ \t]+(?:{NAME_PARTICLE}[ \t]+)?{NAME_TOKEN}){{0,2}}"

IMAGE_OR_SOCIAL_LINE_RE = re.compile(
    r"^\s*(?:[\w\-]+\.(?:jpe?g|png|gif|bmp|svg|webp|ico|pdf|docx?|xlsx?|csv|txt|zip|eml|msg)|"
    r"cid:[^\s]+|linkedin|xing|facebook|instagram|twitter|youtube|tiktok|whatsapp)\s*$",
    re.IGNORECASE,
)

LEGAL_FORM_TAIL_RE = re.compile(
    r"\b(?:GmbH|UG|AG|OHG|KG|GbR|e\.?\s*K\.?|e\.?\s*V\.?|SE|Ltd\.?|Inc\.?|mbH|PartG|KGaA)\b"
)

_DIGIT_RE = re.compile(r"\d")


def has_digit(text: str) -> bool:
    """Extrem schneller C-Check, ob mindestens eine Ziffer im Text vorkommt."""
    return _DIGIT_RE.search(text) is not None


def is_public_authority(raw: str) -> bool:
    low = raw.strip().lower()
    return any(low == p or low.startswith(p + " ") for p in PUBLIC_AUTHORITY_PREFIXES)


@lru_cache(maxsize=4096)
def is_german_compound_non_entity(word: str) -> bool:
    """Erkennt deutsche Geschäfts-/Sach-Komposita (z. B. 'Krankenfahrzeuge', 'Testkennzeichen', 'Anlieferung')."""
    wl = word.lower().strip(".,:;!?()[]{}\"'")
    if len(wl) < 5:
        return False
    if wl in COMMON_FIRST_NAMES or wl in COMMON_SURNAMES or wl in MAJOR_GERMAN_CITIES:
        return False
    for suf in GERMAN_COMPOUND_NOUN_SUFFIXES:
        if wl == suf or (wl.endswith(suf) and len(wl) >= len(suf) + 3):
            return True
    return False


def iter_lines_with_offsets(text: str) -> list[tuple[int, int, str]]:
    """Zerlegt Text einmalig in (start, end, line_without_nl) für Adress- & Signaturblöcke."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        clean = raw_line.rstrip("\r\n")
        out.append((pos, pos + len(clean), clean))
        pos += len(raw_line)
    return out
