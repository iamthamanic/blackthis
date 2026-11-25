"""Offline-Abgleich gegen deutsches Häufigkeitswörterbuch (wordfreq) + Komposita-Zerlegung.

Verhindert False Positives bei normalen deutschen Substantiven & Komposita (z. B. ‚Fluss‘,
‚Sattelzug‘, ‚Baumschnittarbeiten‘, ‚Pelletlieferanten‘, ‚Parktaschen‘), ohne jemals bekannte
Vor-/Nachnamen oder Städte zu blockieren. Läuft 100 % offline mit sauberem Fallback, falls
`wordfreq` noch nicht installiert ist.
"""

from __future__ import annotations

from functools import lru_cache

from .detectors_common import is_german_compound_non_entity
from .lexicons import COMMON_FIRST_NAMES, COMMON_SURNAMES, MAJOR_GERMAN_CITIES, NON_NAME_WORDS

try:
    from wordfreq import zipf_frequency as _zipf_frequency  # type: ignore

    WORDFREQ_AVAILABLE = True
except ImportError:
    _zipf_frequency = None
    WORDFREQ_AVAILABLE = False

# Echte deutsche Nachnamen-Endungen, bei denen eine Komposita-Zerlegung NICHT als Sachwort werten darf
# (z. B. „Neuhaus“, „Goldstein“, „Rosenfeld“, „Bergmann“, „Hofbauer“, „Schönwald“)
_SURNAME_COMPOUND_TAILS = frozenset({
    "mann", "frau", "berg", "berger", "stein", "steiner", "bach", "bacher", "dorf", "dorfer",
    "feld", "felder", "wald", "walder", "baum", "bäume", "haus", "häuser", "hausen", "hof",
    "hofer", "höfer", "meier", "meyer", "maier", "mayer", "müller", "schmidt", "schmitt",
    "weber", "wagner", "becker", "bauer", "fischer", "schulz", "richter", "klein", "wolf",
    "schröder", "neumann", "schwarz", "braun", "krüger", "hartmann", "lange", "werner",
    "krause", "lehmann", "köhler", "herrmann", "walter", "kaiser", "fuchs", "lang", "vogel",
    "jung", "hahn", "keller", "busch", "horn", "brandt", "roth", "beck", "lorenz", "baier",
    "frank", "albrecht", "simon", "ludwig", "winter", "sommer", "könig", "graf", "herzog",
    "ritter", "kaufmann", "zimmermann", "schneider", "koch", "krämer", "schäfer",
})

# Fugenelemente im Deutschen („Produktions-büro“ -> produktion + s + büro)
_FUGEN_ELEMENTS = ("", "s", "n", "es", "en")


@lru_cache(maxsize=8192)
def german_zipf(word_lower: str) -> float:
    """Liefert die Zipf-Worthäufigkeit im Deutschen (0.0 – 8.0), 100 % offline."""
    if _zipf_frequency is None or not word_lower:
        return 0.0
    try:
        return float(_zipf_frequency(word_lower, "de"))
    except Exception:  # noqa: BLE001
        return 0.0


def _is_dictionary_compound(wl: str) -> bool:
    """Prüft, ob ein langes deutsches Wort aus 2 geläufigen deutschen Wörterbuch-Wörtern besteht."""
    n = len(wl)
    if _zipf_frequency is None or n < 8:
        return False

    # Positionen für Split: links >= 4 Zeichen, rechts >= 4 Zeichen
    for split_pos in range(4, n - 3):
        left = wl[:split_pos]
        if german_zipf(left) < 3.1:
            continue
        for fuge in _FUGEN_ELEMENTS:
            r_start = split_pos + len(fuge)
            if r_start > n - 4:
                continue
            if fuge and not wl.startswith(fuge, split_pos):
                continue
            right = wl[r_start:]
            if right in _SURNAME_COMPOUND_TAILS:
                continue
            if german_zipf(right) >= 3.3:
                return True
    return False


@lru_cache(maxsize=8192)
def is_common_german_dictionary_word(word: str) -> bool:
    """True, wenn `word` klar ein normales deutsches Wort/Kompositum ist (und KEIN bekannter Name/Ort)."""
    wl = word.lower().strip(".,:;!?()[]{}\"'")
    if len(wl) < 3:
        return False

    # 1. Bekannte Vor-/Nachnamen & Städte NIEMALS als Sachwort verwerfen!
    if wl in COMMON_FIRST_NAMES or wl in COMMON_SURNAMES or wl in MAJOR_GERMAN_CITIES:
        return False

    # 2. Kuratierte Nicht-Namen & Komposita-Suffixe (funktioniert immer, auch ohne wordfreq)
    if wl in NON_NAME_WORDS or is_german_compound_non_entity(wl):
        return True

    # 3. Offline-Wörterbuch (wordfreq): direktes häufiges deutsches Wort oder deutsches Kompositum
    if _zipf_frequency is not None:
        if german_zipf(wl) >= 3.0:
            return True
        if _is_dictionary_compound(wl):
            return True

    return False
