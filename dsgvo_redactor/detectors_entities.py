"""Kontext- & Lexikon-Detektoren für Berufsbezeichnungen, Klarnamen und Firmennamen."""

from __future__ import annotations

import re
from functools import lru_cache

from .detectors_addresses import STREET_COMPOUND_RE, STREET_PREFIX_RE, is_plausible_street
from .detectors_common import (
    IMAGE_OR_SOCIAL_LINE_RE,
    LEGAL_FORM_TAIL_RE,
    NAME_PARTICLE,
    NAME_SEQ,
    NAME_TOKEN,
    NAME_TOKEN_CI,
    NAME_UPPER_CHARS,
    is_german_compound_non_entity,
    is_public_authority,
    iter_lines_with_offsets,
)
from .detectors_structured import BARE_DOMAIN_RE, EMAIL_RE, URL_FULL_RE
from .german_dict import is_common_german_dictionary_word
from .lexicons import (
    COMMON_FIRST_NAMES,
    COMMON_SURNAMES,
    FREEMAIL_DOMAIN_STEMS,
    JOB_COMPOUND_SUFFIXES,
    JOB_NON_MATCHES,
    JOB_TITLES_EXACT,
    MAJOR_GERMAN_CITIES,
    NON_NAME_WORDS,
    ORG_NOISE_WORDS,
)
from .models import Category, Confidence, SpanMatch

# --- 1. Job-Positionen & Berufsbezeichnungen (mit O(1)-LRU-Cache) ---

_MULTIWORD_JOB_TITLES = sorted([t for t in JOB_TITLES_EXACT if " " in t], key=len, reverse=True)
_MULTIWORD_JOB_RE = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in _MULTIWORD_JOB_TITLES) + r")\b", re.IGNORECASE)

_DYNAMIC_JOB_PHRASE_RE = re.compile(
    r"\b(?:(?:Senior|Junior|Stellv\.?|Stellvertretende[rn]?|Kaufmännische[rn]?|Technische[rn]?|Leitende[rn]?)\s+)?"
    r"(?:Head\s+of\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß&\-\s]{2,35}|"
    r"Team\s*Lead(?:er)?\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß&\-]{2,25}|"
    r"Leiter(?:in)?\s+(?:der\s+|des\s+)?[A-ZÄÖÜ][a-zäöüß\-]{3,28}|"
    r"Leitung\s+(?:der\s+|des\s+)?[A-ZÄÖÜ][a-zäöüß\-]{3,28}|"
    r"Assistenz\s+(?:der|des)\s+[A-ZÄÖÜ][a-zäöüß\-]{3,28})",
)

_SINGLE_JOB_WORDS = frozenset(t for t in JOB_TITLES_EXACT if " " not in t)
_WORD_TOKEN_RE = re.compile(r"\b[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*(?:[\*/:_](?:in|innen|r|m/w/d))?\b")
_GENDER_SUFFIX_RE = re.compile(r"[\*/:_](in|innen)$")


@lru_cache(maxsize=4096)
def normalize_job_token(word: str) -> str:
    w = word.lower()
    if "*" in w or "/" in w or ":" in w or "_" in w:
        w = _GENDER_SUFFIX_RE.sub(r"\1", w)
    return w


@lru_cache(maxsize=4096)
def matches_job_stem(norm: str) -> bool:
    if not norm or norm in JOB_NON_MATCHES:
        return False
    if norm in _SINGLE_JOB_WORDS:
        return True
    for suffix_strip in ("s", "n", "en", "nen"):
        if norm.endswith(suffix_strip) and len(norm) > len(suffix_strip) + 4:
            stem = norm[:-len(suffix_strip)]
            if stem in _SINGLE_JOB_WORDS and stem not in JOB_NON_MATCHES:
                return True
    for suf in JOB_COMPOUND_SUFFIXES:
        if norm.endswith(suf) and len(norm) >= len(suf) + 3:
            return True
        if norm.endswith(suf + "s") or norm.endswith(suf + "n"):
            base = norm[:-1]
            if len(base) >= len(suf) + 3 and base not in JOB_NON_MATCHES:
                return True
        elif norm.endswith(suf + "en"):
            base = norm[:-2]
            if len(base) >= len(suf) + 3 and base not in JOB_NON_MATCHES:
                return True
    return False


def detect_job_titles(text: str) -> list[SpanMatch]:
    matches: list[SpanMatch] = []

    for m in _MULTIWORD_JOB_RE.finditer(text):
        matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.JOB_TITLE, Confidence.HIGH, "Berufsbezeichnung (Mehrwort)"))

    for m in _DYNAMIC_JOB_PHRASE_RE.finditer(text):
        raw = m.group(0).strip()
        matches.append(SpanMatch(m.start(), m.start() + len(raw), raw, Category.JOB_TITLE, Confidence.HIGH, "Job-Position (Phrase)"))

    for m in _WORD_TOKEN_RE.finditer(text):
        word = m.group(0)
        if len(word) <= 3 and not word.isupper():
            continue
        norm = normalize_job_token(word)
        if norm in NON_NAME_WORDS:
            continue
        if matches_job_stem(norm):
            if text[m.end():m.end() + 6].lower().startswith("-notiz"):
                continue
            matches.append(SpanMatch(m.start(), m.end(), word, Category.JOB_TITLE, Confidence.HIGH, "Berufsbezeichnung / Position"))

    return matches


# --- 2. Klarnamen (alle Regexes einmalig vorkompiliert) ---

_SALUTATION_RE = re.compile(
    r"\b(?:Sehr[ \t]+geehrte[rn]?|Liebe[rn]?|Hallo|Guten[ \t]+Tag|Guten[ \t]+Morgen|Guten[ \t]+Abend|"
    r"Hi|Hey|Moin|Servus|Grüß[ \t]+Gott)[ \t]*,?[ \t]+"
    r"(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+)?"
    r"(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?"
    rf"({NAME_SEQ})",
)
_ROUTING_CONTEXT_RE = re.compile(
    r"\b(?:zu[ \t]+Händen|z\.?[ \t]*Hd\.?|z\.?[ \t]*H\.?|Ansprechpartner(?:in)?|Sachbearbeiter(?:in)?|"
    r"Sachbearbeitung|Bearbeiter(?:in)?|Bearbeitung|Kolleg(?:e|in)|Kontoinhaber(?:in)?|Antragsteller(?:in)?|"
    r"Kundenberater(?:in)?|Disponent(?:in)?|Geschäftsführer(?:in)?|Projektleiter(?:in)?|"
    r"Teamleiter(?:in)?|Bauleiter(?:in)?|Fahrer(?:in)?|Inhaber(?:in)?|Bevollmächtigte[rn]?|"
    r"Vertreter(?:in)?|Vertretung|Kontaktperson|Ansprechperson|Auftraggeber(?:in)?|"
    r"Rechnungsempfänger(?:in)?|Empfänger(?:in)?|Absender(?:in)?|Verfasser(?:in)?|"
    r"Mieter(?:in)?|Vermieter(?:in)?|Eigentümer(?:in)?|Hausmeister(?:in)?|Zeug(?:e|in)|"
    r"Erstellt[ \t]+von|Bearbeitet[ \t]+von|Geändert[ \t]+von|Zugewiesen[ \t]+an|"
    r"Gezeichnet|gez\.)[ \t]*[:,\-]?[ \t]+"
    r"(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+)?"
    r"(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?"
    rf"({NAME_SEQ})",
)
_FIELD_LABEL_NAME_RE = re.compile(
    r"(?im)(?:^|(?<=[\n;|]))[ \t]*(?:Name|Vorname|Nachname|Vollständiger[ \t]+Name|Kunde|Kundin|"
    r"Kontakt|Von|From|An|To|Cc|Mitarbeiter(?:in)?|Berater(?:in)?|Verantwortlich(?:er|e)?|"
    r"Zuständig(?:er|e)?|Unterschrift|Rückruf(?:wunsch)?)[ \t]*:[ \t]*"
    r"(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+)?"
    r"(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?"
    rf"({NAME_SEQ})",
)
_PASSIVE_AGENT_RE = re.compile(
    r"\b(?:durch|von)[ \t]+(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+)?(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?"
    rf"({NAME_SEQ})[ \t]+"
    r"(?:genehmigt|bearbeitet|bestätigt|geprüft|freigegeben|abgelehnt|unterschrieben|"
    r"erstellt|beantragt|übernommen|abgesprochen|geklärt|erledigt|storniert|weitergeleitet|veranlasst)\b",
)
_TALKED_TO_RE = re.compile(
    r"\b(?:"
    r"(?:Rücksprache|Gespräch|Telefonat|Kontakt|Absprache|Abstimmung|Termin|telefoniert|gesprochen)[ \t]+mit|"
    r"(?:Anruf|Rückruf|Nachricht|E-?Mail|Mail|Info|Rückmeldung|Bestätigung|Freigabe|Anfrage|Antwort|Hinweis|Beschwerde)[ \t]+(?:von|an|für)|"
    r"(?:weitergeleitet|geschrieben|gesendet|geschickt|übergeben)[ \t]+an|"
    r"laut"
    r")[ \t]+"
    r"(?:(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+)?(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?"
    rf"({NAME_SEQ})",
)
_PERSON_ACTION_RE = re.compile(
    rf"(?:^|(?<=[\n.!?;:\-][ \t])|(?<=[\n]))({NAME_SEQ})[ \t]+"
    r"(?:hat[ \t]+(?:angerufen|zurückgerufen|geschrieben|geantwortet|gefragt|bestätigt|storniert|geklärt|mitgeteilt|zugesagt|abgesagt|überwiesen|bezahlt|beantragt)|"
    r"rief[ \t]+(?:an|zurück)|meldet[ \t]+sich|meldete[ \t]+sich|ruft[ \t]+(?:an|zurück)|"
    r"bittet[ \t]+um|bat[ \t]+um|fragt[ \t]+nach|fragte[ \t]+nach|teilte[ \t]+mit|teilt[ \t]+mit|"
    r"schrieb\b|sagte\b|wollte[ \t]+wissen|möchte[ \t]+(?:wissen|stornieren|verschieben))\b"
)
_HONORIFIC_NAME_RE = re.compile(
    rf"\b(?:Herrn?|Frau|Hr\.?|Fr\.?)[ \t]+(?:(?:Prof\.?[ \t]*)?(?:Dr\.?[ \t]*)?)?({NAME_SEQ})",
)
_HONORIFIC_LOWER_RE = re.compile(
    rf"\b(herrn?|frau|hr\.|fr\.)[ \t]+({NAME_TOKEN_CI}(?:[ \t]+{NAME_TOKEN_CI})?)\b"
)
_TITLE_NAME_RE = re.compile(
    rf"\b(?:Prof\.?[ \t]+Dr\.?|Prof\.?|Dr\.?(?:[ \t]+(?:med|jur|rer\.[ \t]*nat|phil|ing)\.?)?)[ \t]+({NAME_SEQ})",
)
_SIGN_AUTH_RE = re.compile(rf"\b(?:i\.?[ \t]*A\.?|i\.?[ \t]*V\.?|ppa\.?)[ \t]+({NAME_SEQ})")
_SIGNOFF_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:Mit[ \t]+freundlichen[ \t]+Grüßen|Mit[ \t]+freundlichem[ \t]+Gruß|"
    r"Mit[ \t]+kollegialen[ \t]+Grüßen|Freundliche[ \t]+Grüße|Freundlichen[ \t]+Gruß|"
    r"Viele[ \t]+Grüße|Beste[ \t]+Grüße|Herzliche[ \t]+Grüße|Liebe[ \t]+Grüße|Lieben[ \t]+Gruß|"
    r"Schöne[ \t]+Grüße|Sonnige[ \t]+Grüße|Alles[ \t]+Gute|Hochachtungsvoll|"
    r"MfG|VG|BG|LG|Grüße|Gruß|Best[ \t]+regards|Kind[ \t]+regards|Warm[ \t]+regards|Regards|"
    r"Vielen[ \t]+Dank(?:[ \t]+(?:vorab|und[ \t]+(?:viele|freundliche|beste|liebe)[ \t]+Grüße))?)"
    r"(?:[ \t]+aus[ \t]+[A-ZÄÖÜ][a-zäöüß\-]+)?[ \t,!.]*$"
)
_INLINE_SIGNOFF_RE = re.compile(
    r"\b(?:Mit[ \t]+freundlichen[ \t]+Grüßen|Viele[ \t]+Grüße|Beste[ \t]+Grüße|Liebe[ \t]+Grüße|"
    rf"Herzliche[ \t]+Grüße|Freundliche[ \t]+Grüße|MfG|VG|BG|LG|Grüße|Gruß)[ \t]*,?[ \t]+({NAME_SEQ})"
)
_TWO_CAP_WORDS_RE = re.compile(rf"\b(?=({NAME_TOKEN})[ \t]+(?:({NAME_PARTICLE})[ \t]+)?({NAME_TOKEN})\b)")
_ALLCAPS_TWO_WORDS_RE = re.compile(rf"\b(?=([{NAME_UPPER_CHARS}]{{2,}})[ \t]+([{NAME_UPPER_CHARS}]{{2,}}(?:-[{NAME_UPPER_CHARS}]{{2,}})?)\b)")
_ALLCAPS_HONORIFIC_RE = re.compile(rf"\b(?:HERRN?|FRAU|HR\.?|FR\.?)[ \t]+([{NAME_UPPER_CHARS}]{{2,}}(?:[ \t]+[{NAME_UPPER_CHARS}]{{2,}})?)\b")
_INITIAL_SURNAME_RE = re.compile(rf"\b([{NAME_UPPER_CHARS}]\.)[ \t]+({NAME_TOKEN})\b")
_SURNAME_COMMA_FIRST_RE = re.compile(rf"\b({NAME_TOKEN}),[ \t]+({NAME_TOKEN})\b")
_ALLCAPS_SURNAME_COMMA_FIRST_RE = re.compile(rf"\b([{NAME_UPPER_CHARS}]{{2,}}),[ \t]+([{NAME_UPPER_CHARS}]{{2,}})\b")
_PREP_SINGLE_NAME_RE = re.compile(rf"\b(bei|mit|von|durch|an|für)[ \t]+({NAME_TOKEN})\b")
_STRICT_PREP_KNOWN_ONLY = frozenset({"für", "mit"})
_CONTACT_LINE_HINT_RE = re.compile(
    r"(?i)(?:@|https?://|www\.|\b(?:Tel(?:efon)?\.?|Mobil|Handy|Fax|Durchwahl|E-?Mail)\s*[:.]|(?:\+|00)49|\b0[1-9]\d{1,4}\s*[/\-])"
)

# Vorkompilierte Helfer (früher fälschlich in Schleifen neu kompiliert!)
_SINGLE_INITIAL_RE = re.compile(r"^[A-ZÄÖÜ]\.?$")
_SIG_SKIP_CHARS_RE = re.compile(r"[@\d]|https?://|www\.", re.IGNORECASE)
_SIG_SKIP_COLON_RE = re.compile(r"[@\d:]|https?://|www\.", re.IGNORECASE)
_SIG_NAME_TRAILING_RE = re.compile(rf"^(?:(?:Prof\.?\s*)?(?:Dr\.?\s*)?)?({NAME_SEQ})(?:\s*[,|/\-].*)?$")
_SIG_NAME_EXACT_RE = re.compile(rf"^(?:(?:Prof\.?\s*)?(?:Dr\.?\s*)?)?({NAME_SEQ})$")
_SIG_ALLCAPS_NAME_RE = re.compile(
    rf"^([{NAME_UPPER_CHARS}]{{2,}}(?:-[{NAME_UPPER_CHARS}]{{2,}})?(?:[ \t]+[{NAME_UPPER_CHARS}]{{2,}}(?:-[{NAME_UPPER_CHARS}]{{2,}})?){{1,2}})$"
)
_AMBIGUOUS_FIRST_NAMES = frozenset({"mai", "august", "ernst", "frank", "mark", "christian", "peter"})
_STREET_SUFFIX_CHECK = ("straße", "strasse", "str", "allee", "weg", "platz", "gasse", "ring")

_HIGH_CONTEXT_NAME_RULES = (
    (_SALUTATION_RE, "Name (Anrede)"),
    (_ROUTING_CONTEXT_RE, "Name (Zuordnung/Kontext)"),
    (_FIELD_LABEL_NAME_RE, "Name (Feld-Label)"),
    (_PASSIVE_AGENT_RE, "Name (Handlungsträger 'durch/von ...')"),
    (_TALKED_TO_RE, "Name (Gesprächspartner / Kommunikation)"),
    (_HONORIFIC_NAME_RE, "Name (Herr/Frau)"),
    (_TITLE_NAME_RE, "Name (Titel Dr./Prof.)"),
    (_SIGN_AUTH_RE, "Name (Zeichnungsbefugnis i.A./i.V.)"),
    (_INLINE_SIGNOFF_RE, "Name (Grußformel einzeilig)"),
)


def _trim_name_candidate(raw: str, *, allow_dict_word: bool = False) -> str:
    words = raw.split()
    kept: list[str] = []
    for idx, w in enumerate(words):
        wl = w.lower().rstrip(".,:;!?")
        is_known_surname = wl in COMMON_SURNAMES
        is_known_first = wl in COMMON_FIRST_NAMES
        if idx == 0 and (
            (wl in NON_NAME_WORDS and not is_known_surname and not is_known_first)
            or is_german_compound_non_entity(w)
            or (not allow_dict_word and is_common_german_dictionary_word(w))
        ):
            return ""
        if idx > 0 and (
            (wl in NON_NAME_WORDS and not is_known_surname)
            or wl in MAJOR_GERMAN_CITIES
            or LEGAL_FORM_TAIL_RE.fullmatch(w)
            or is_german_compound_non_entity(w)
            or (not allow_dict_word and is_common_german_dictionary_word(w))
        ):
            break
        if idx > 0 and matches_job_stem(normalize_job_token(w)) and not is_known_surname:
            break
        kept.append(w)
    if not kept:
        return ""
    if len(kept) == 1 and kept[0].lower() in NON_NAME_WORDS and kept[0].lower() not in COMMON_SURNAMES:
        return ""
    if all(
        (w.lower() in NON_NAME_WORDS and w.lower() not in COMMON_SURNAMES and w.lower() not in COMMON_FIRST_NAMES)
        or w.lower() in MAJOR_GERMAN_CITIES
        for w in kept
    ):
        return ""
    if all(w.isupper() and len(w) >= 2 for w in kept):
        return ""
    return " ".join(kept)


def detect_names(text: str, *, lines_cache: list[tuple[int, int, str]] | None = None) -> list[SpanMatch]:
    matches: list[SpanMatch] = []

    def add_candidate(raw: str, raw_start: int, confidence: Confidence, rule: str, *, allow_dict_word: bool = False) -> None:
        cleaned = _trim_name_candidate(raw, allow_dict_word=allow_dict_word)
        if not cleaned or len(cleaned) < 2 or _SINGLE_INITIAL_RE.match(cleaned):
            return
        matches.append(SpanMatch(raw_start, raw_start + len(cleaned), cleaned, Category.NAME, confidence, rule))

    # 1. Kontext-Trigger mit hoher Konfidenz
    for regex, rule in _HIGH_CONTEXT_NAME_RULES:
        is_honorific_rule = regex in (_HONORIFIC_NAME_RE, _TITLE_NAME_RE)
        for m in regex.finditer(text):
            add_candidate(m.group(1), m.start(1), Confidence.HIGH, rule, allow_dict_word=is_honorific_rule)

    # 1b. Kleingeschriebene Anreden („herr schmidt“, „fr. becker“ – aber kein „fr. tourenplanung“ = Freitag!)
    for m in _HONORIFIC_LOWER_RE.finditer(text):
        hon = m.group(1).lower()
        raw = m.group(2)
        if raw[0].isupper():
            continue
        kept: list[str] = []
        for idx, w in enumerate(raw.split()):
            wl = w.lower()
            # Bei „fr.“ (oft Abkürzung für „Freitag“) nur bekannte Vor-/Nachnamen akzeptieren!
            if hon == "fr." and idx == 0 and wl not in COMMON_SURNAMES and wl not in COMMON_FIRST_NAMES:
                break
            if (
                (wl in NON_NAME_WORDS and wl not in COMMON_SURNAMES)
                or wl in MAJOR_GERMAN_CITIES
                or matches_job_stem(normalize_job_token(w))
                or is_common_german_dictionary_word(w)
            ):
                break
            kept.append(w)
        if kept:
            cleaned = " ".join(kept)
            if len(cleaned) >= 3:
                matches.append(SpanMatch(m.start(2), m.start(2) + len(cleaned), cleaned, Category.NAME, Confidence.HIGH, "Name (Anrede kleingeschrieben)"))

    # 1c. CRM-Subjekt vor Personen-Verb
    for m in _PERSON_ACTION_RE.finditer(text):
        cleaned = _trim_name_candidate(m.group(1))
        if not cleaned or len(cleaned) < 3:
            continue
        tokens = cleaned.split()
        if any(t.lower() in MAJOR_GERMAN_CITIES for t in tokens):
            continue
        if len(tokens) == 1 and matches_job_stem(normalize_job_token(tokens[0])):
            continue
        is_known = len(tokens) >= 2 or tokens[0].lower() in COMMON_FIRST_NAMES or tokens[0].lower() in COMMON_SURNAMES
        matches.append(SpanMatch(m.start(1), m.start(1) + len(cleaned), cleaned, Category.NAME, Confidence.HIGH if is_known else Confidence.REVIEW, "Name (Handlungsträger / CRM-Notiz)"))

    # 1d. GROSSBUCHSTABEN-Namen
    for m in _ALLCAPS_HONORIFIC_RE.finditer(text):
        words = [w for w in m.group(1).split() if w.lower() not in NON_NAME_WORDS or w.lower() in COMMON_SURNAMES]
        if words and not LEGAL_FORM_TAIL_RE.fullmatch(words[0]):
            cleaned = " ".join(words)
            if len(cleaned) >= 3:
                matches.append(SpanMatch(m.start(1), m.start(1) + len(cleaned), cleaned, Category.NAME, Confidence.HIGH, "Name (Herr/Frau GROSSBUCHSTABEN)"))

    for m in _ALLCAPS_TWO_WORDS_RE.finditer(text):
        w1, w2 = m.group(1), m.group(2)
        w1_l, w2_l = w1.lower(), w2.lower()
        if w1_l in COMMON_FIRST_NAMES and w1_l not in NON_NAME_WORDS:
            w2_ok = (w2_l in COMMON_SURNAMES) or (
                w2_l not in NON_NAME_WORDS
                and w2_l not in MAJOR_GERMAN_CITIES
                and not LEGAL_FORM_TAIL_RE.fullmatch(w2)
                and not matches_job_stem(normalize_job_token(w2))
                and len(w2) >= 3
            )
            if w2_ok:
                s_start, s_end = m.start(1), m.end(2)
                matches.append(SpanMatch(s_start, s_end, text[s_start:s_end], Category.NAME, Confidence.HIGH, "Name (Vorname + Nachname GROSSBUCHSTABEN)"))

    for m in _ALLCAPS_SURNAME_COMMA_FIRST_RE.finditer(text):
        s_l, f_l = m.group(1).lower(), m.group(2).lower()
        if f_l in COMMON_FIRST_NAMES and (s_l in COMMON_SURNAMES or (s_l not in NON_NAME_WORDS and s_l not in MAJOR_GERMAN_CITIES)):
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.NAME, Confidence.HIGH, "Name (Nachname, Vorname GROSSBUCHSTABEN)"))

    # 2. Grußformeln & E-Mail-Signaturen (nur wenn mehrzeilig)
    if "\n" in text:
        for sm in _SIGNOFF_LINE_RE.finditer(text):
            pos = sm.end()
            offset = pos
            inspected = 0
            for line in text[pos:].splitlines(keepends=True):
                stripped = line.strip()
                line_start = offset + (len(line) - len(line.lstrip(" \t")))
                offset += len(line)
                if not stripped or IMAGE_OR_SOCIAL_LINE_RE.match(stripped):
                    continue
                inspected += 1
                if inspected > 3:
                    break
                if LEGAL_FORM_TAIL_RE.search(stripped) or _SIG_SKIP_CHARS_RE.search(stripped) or matches_job_stem(normalize_job_token(stripped)):
                    continue
                m_sig = _SIG_NAME_TRAILING_RE.match(stripped)
                if m_sig:
                    cand = _trim_name_candidate(m_sig.group(1))
                    if cand:
                        tokens = cand.split()
                        conf = Confidence.HIGH if (len(tokens) >= 2 or cand.lower() in COMMON_FIRST_NAMES or cand.lower() in COMMON_SURNAMES) else Confidence.REVIEW
                        name_start = line_start + m_sig.start(1)
                        matches.append(SpanMatch(name_start, name_start + len(cand), cand, Category.NAME, conf, "Name (E-Mail-Signatur)"))
                        break

        # 2b. Signatur-Name direkt über Kontakt- oder Positionszeile
        line_recs = lines_cache if lines_cache is not None else iter_lines_with_offsets(text)
        for idx, (ln_start, _ln_end, ln) in enumerate(line_recs):
            stripped = ln.strip()
            if not stripped or IMAGE_OR_SOCIAL_LINE_RE.match(stripped):
                continue
            if LEGAL_FORM_TAIL_RE.search(stripped) or _SIG_SKIP_COLON_RE.search(stripped) or matches_job_stem(normalize_job_token(stripped)):
                continue
            m_sig = _SIG_NAME_EXACT_RE.match(stripped)
            cand = ""
            m_start = 0
            if m_sig:
                cand = _trim_name_candidate(m_sig.group(1))
                m_start = m_sig.start(1)
            else:
                m_caps = _SIG_ALLCAPS_NAME_RE.match(stripped)
                if m_caps:
                    caps_words = m_caps.group(1).split()
                    if (
                        len(caps_words) >= 2
                        and not all(w.lower() in NON_NAME_WORDS or w.lower() in MAJOR_GERMAN_CITIES for w in caps_words)
                        and not any(matches_job_stem(normalize_job_token(w)) for w in caps_words)
                    ):
                        cand = m_caps.group(1)
                        m_start = m_caps.start(1)
            if not cand or len(cand.split()) < 2:
                continue
            next_non_empty: list[str] = []
            for _, _, nln in line_recs[idx + 1:]:
                ns = nln.strip()
                if ns:
                    next_non_empty.append(ns)
                    if len(next_non_empty) >= 2:
                        break
            if any(
                _CONTACT_LINE_HINT_RE.search(ns)
                or matches_job_stem(normalize_job_token(ns))
                or _MULTIWORD_JOB_RE.search(ns)
                for ns in next_non_empty
            ):
                line_lead = len(ln) - len(ln.lstrip(" \t"))
                name_start = ln_start + line_lead + m_start
                matches.append(SpanMatch(name_start, name_start + len(cand), cand, Category.NAME, Confidence.HIGH, "Name (Signaturblock über Kontakt/Position)"))

    # 3. Lexikon-Vollname im Fließtext
    for m in _TWO_CAP_WORDS_RE.finditer(text):
        w1, w2 = m.group(1), m.group(3)
        w1_l, w2_l = w1.lower(), w2.lower()
        if w1_l in COMMON_FIRST_NAMES and w1_l not in NON_NAME_WORDS:
            w2_ok = (w2_l in COMMON_SURNAMES) or (
                w2_l not in NON_NAME_WORDS
                and w2_l not in MAJOR_GERMAN_CITIES
                and not LEGAL_FORM_TAIL_RE.fullmatch(w2)
                and not matches_job_stem(normalize_job_token(w2))
            )
            if w2_ok:
                span_start, span_end = m.start(1), m.end(3)
                conf = Confidence.REVIEW if (w1_l in _AMBIGUOUS_FIRST_NAMES and w2_l not in COMMON_SURNAMES) else Confidence.HIGH
                matches.append(SpanMatch(span_start, span_end, text[span_start:span_end], Category.NAME, conf, "Name (Vorname + Nachname)"))

    # 4. Initiale + Nachname („M. Müller“)
    if "." in text:
        for m in _INITIAL_SURNAME_RE.finditer(text):
            surname = m.group(2)
            sl = surname.lower()
            if len(surname) < 3 or surname.isupper() or sl in NON_NAME_WORDS or sl in MAJOR_GERMAN_CITIES:
                continue
            if LEGAL_FORM_TAIL_RE.fullmatch(surname) or matches_job_stem(normalize_job_token(surname)):
                continue
            if sl.endswith(_STREET_SUFFIX_CHECK):
                continue
            conf = Confidence.HIGH if sl in COMMON_SURNAMES else Confidence.REVIEW
            matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.NAME, conf, "Name (Initiale + Nachname)"))

    # 5. „Müller, Thomas“
    if "," in text:
        for m in _SURNAME_COMMA_FIRST_RE.finditer(text):
            s_l, f_l = m.group(1).lower(), m.group(2).lower()
            if f_l in COMMON_FIRST_NAMES and (s_l in COMMON_SURNAMES or (s_l not in NON_NAME_WORDS and s_l not in MAJOR_GERMAN_CITIES)):
                matches.append(SpanMatch(m.start(), m.end(), m.group(0), Category.NAME, Confidence.HIGH, "Name (Nachname, Vorname)"))

    # 6. DSGVO-Sicherheitsnetz nach Präposition (mit Wörterbuch- & Komposita-Gegenprüfung!)
    for m in _PREP_SINGLE_NAME_RE.finditer(text):
        prep_l = m.group(1).lower()
        word = m.group(2)
        wl = word.lower()
        if len(word) < 3 or word.isupper():
            continue
        if text[m.end(2):m.end(2) + 1] == ":":
            continue
        is_known = wl in COMMON_FIRST_NAMES or wl in COMMON_SURNAMES
        # Nach „für“ / „mit“ stehen im Deutschen zu ~95 % Sachwörter („für Parktaschen“, „mit Sattelzug“) -> nur bekannte Namen!
        if prep_l in _STRICT_PREP_KNOWN_ONLY and not is_known:
            continue
        if (
            wl in NON_NAME_WORDS
            or wl in MAJOR_GERMAN_CITIES
            or matches_job_stem(normalize_job_token(word))
            or is_common_german_dictionary_word(word)
        ):
            continue
        if LEGAL_FORM_TAIL_RE.fullmatch(word) or wl.endswith(_STREET_SUFFIX_CHECK):
            continue
        conf = Confidence.HIGH if is_known else Confidence.REVIEW
        matches.append(SpanMatch(m.start(2), m.end(2), word, Category.NAME, conf, "Name (nach Präposition, DSGVO-Sicherheitsnetz)"))

    return matches


# --- 3. Firmennamen / Organisationen (7 Kontext-Schichten, komplett vorkompiliert) ---

_LEGAL_FORM_FULL_ALT = (
    r"g?GmbH(?:[ \t]*&[ \t]*Co\.?[ \t]*(?:KG|OHG|oHG))?"
    r"|g?UG(?:[ \t]*\(haftungsbeschränkt\))?"
    r"|AG(?:[ \t]*&[ \t]*Co\.?[ \t]*KGa?A?)?"
    r"|SE(?:[ \t]*&[ \t]*Co\.?[ \t]*KGaA)?"
    r"|OHG|oHG|GbR|KGaA|PartG(?:mbB)?|mbH"
    r"|e\.?[ \t]*K(?:fm|fr)?\.?|e\.?[ \t]*V\.?|e\.?[ \t]*G\.?"
    r"|Co\.?[ \t]*KG|KG"
    r"|Ltd\.?|Inc\.?|LLC|LLP|PLC|Corp\.?|B\.?V\.?|N\.?V\.?|S\.?A\.?|S\.?r\.?l\.?|Sp\.[ \t]*z[ \t]*o\.?o\.?"
)
_LEGAL_FORM_FULL_RE = re.compile(rf"\b(?:{_LEGAL_FORM_FULL_ALT})\b")

_COMPANY_LEGAL_RE = re.compile(
    r"(?:^|(?<=[\s,;:(\-\"']))"
    rf"([A-ZÄÖÜ0-9][\wÄÖÜäöüß&\.\-/]+(?:[ \t]+(?:&[ \t]+)?[A-ZÄÖÜ0-9a-zäöüß][\wÄÖÜäöüß&\.\-/]*){{0,5}}[ \t]+"
    rf"(?:{_LEGAL_FORM_FULL_ALT}))(?![A-Za-zÄÖÜäöüß0-9])"
)
_COMPANY_LABEL_RE = re.compile(
    r"(?im)(?:^|(?<=[\n;|,]))[ \t]*(?:Firma|Firmenname|Unternehmen|Gesellschaft|Betrieb|"
    r"Arbeitgeber|Auftraggeber|Geschäftspartner|Partnerfirma|Subunternehmer|Dienstleister|"
    r"Lieferant|Spedition|Umzugsfirma|Umzugsunternehmen|Transportunternehmen|Logistikpartner|"
    r"Kanzlei|Steuerkanzlei|Praxis|Hausverwaltung|Immobilienverwaltung|Baufirma|Bauunternehmen|"
    r"Handwerksbetrieb|Kundenfirma|Rechnungsfirma|Company|Organisation|Organization)[ \t]*:[ \t]*"
    r"([^\n;|]{2,80})"
)
_COMPANY_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"(?:Wir[ \t]+sind|Ich[ \t]+bin|Anruf|Rückruf|Mail|E-?Mail|Nachricht|Rechnung|Angebot|Anfrage|Mitarbeiter(?:in)?)[ \t]+(?:von|bei)[ \t]+(?:der[ \t]+|dem[ \t]+)?"
    r"|(?:im[ \t]+Auftrag|i\.?[ \t]*A\.?|namens)[ \t]+(?:der|des|von)[ \t]+(?:Firma[ \t]+)?"
    r"|(?:arbeitet|angestellt|beschäftigt)[ \t]+bei[ \t]+(?:der[ \t]+|dem[ \t]+)?"
    r"|(?:über|durch|bei|mit|von|an|für)[ \t]+(?:die[ \t]+|der[ \t]+)?(?:Firma|Fa\.|Spedition|Umzugsfirma|Baufirma|Hausverwaltung|Kanzlei)[ \t]+"
    r"|(?:Vertrag|Zusammenarbeit|Abrechnung)[ \t]+mit[ \t]+(?:der[ \t]+|dem[ \t]+)?"
    r"|(?:Firma|Fa\.|Unternehmen|Spedition|Umzugsfirma|Umzugsunternehmen|Transportfirma|Transportunternehmen|"
    r"Baufirma|Bauunternehmen|Reinigungsfirma|Hausverwaltung|Immobilienverwaltung|Kanzlei|Steuerkanzlei|"
    r"Praxis|Autohaus|Hotel|Agentur|Atelier|Studio|Werkstatt|Schreinerei|Tischlerei|Malerbetrieb|"
    r"Elektrobetrieb|Subunternehmer|Dienstleister|Partnerfirma)[ \t]+"
    r")"
    r"([A-ZÄÖÜ0-9][\wÄÖÜäöüß&\.\-/]+(?:[ \t]+(?:&[ \t]+)?[A-ZÄÖÜ0-9a-zäöüß][\wÄÖÜäöüß&\.\-/]*){0,5})"
)
_COMPANY_AMP_RE = re.compile(
    r"\b([A-ZÄÖÜ][\wÄÖÜäöüß\-]+(?:[ \t]+[A-ZÄÖÜ][\wÄÖÜäöüß\-]+)?[ \t]*&[ \t]*"
    r"(?:Söhne|Soehne|Partner|Kollegen|Co\.?|Cie\.?|Brüder|Associates|Friends))\b"
)
_COMPANY_INDUSTRY_WORDS = (
    r"Umzüge|Umzugsservice|Umzugslogistik|Umzugsunternehmen|Umzugsfirma|"
    r"Transporte|Transportservice|Möbeltransporte|Logistik|Logistics|Spedition|"
    r"Kurierdienst|Expressdienst|Fuhrparkservice|Immobilien|Hausverwaltung|Objektverwaltung|"
    r"Gebäudereinigung|Gebäudeservice|Facility[ \t]+Management|Gerüstbau|Tiefbau|Hochbau|"
    r"Straßenbau|Gartenbau|Landschaftsbau|Metallbau|Stahlbau|Elektrotechnik|Haustechnik|"
    r"Klimatechnik|Sanitärtechnik|Bauunternehmen|Bauunternehmung|Handelsgesellschaft|"
    r"Vertriebsgesellschaft|Verwaltungsgesellschaft|Dienstleistungen|Services|Solutions|"
    r"Consulting|Systems|Group|Gruppe|Holding|Ventures|Industries|Enterprises|"
    r"Rechtsanwälte|Steuerberatung|Wirtschaftsprüfung|Architekten|Ingenieurbüro|"
    r"Planungsbüro|Maklerbüro|Autovermietung|Baumaschinen|Containerdienst|"
    r"Sicherheitsdienst|Wachdienst|Personalservice|Zeitarbeit|Montageservice|"
    r"Handwerkerservice|Malerbetrieb|Dachdeckerei|Schlosserei|Tischlerei|Schreinerei|"
    r"Zimmerei|Druckerei|Autohaus|Werkstatt"
)
_COMPANY_INDUSTRY_WORD_RE = re.compile(rf"\b(?:{_COMPANY_INDUSTRY_WORDS})\b")
_COMPANY_INDUSTRY_PHRASE_RE = re.compile(
    rf"\b([A-ZÄÖÜ0-9][\wÄÖÜäöüß&\.\-]+(?:[ \t]+(?:&[ \t]+)?[A-ZÄÖÜ0-9][\wÄÖÜäöüß&\.\-]+){{0,2}}[ \t]+(?:{_COMPANY_INDUSTRY_WORDS}))\b"
)

_COMPANY_DROP_PREFIX = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "einem", "einen",
    "von", "vom", "bei", "beim", "mit", "für", "an", "am", "auf", "in", "im", "zu", "zum", "zur",
    "über", "durch", "und", "oder", "aber", "sind", "ist", "war", "wir", "sie", "ich", "er", "es",
    "hat", "haben", "wird", "wurde", "bitte", "danke", "hallo", "moin", "sehr", "geehrte", "geehrter",
    "firma", "fa.", "fa", "unternehmen", "kunde", "kunden", "auftraggeber", "partner", "laut", "gemäß",
    "hier", "dort", "heute", "morgen", "wegen", "trotz", "sowie", "dass", "weil", "wenn", "ob",
    "anruf", "mail", "e-mail", "nachricht", "rechnung", "angebot", "anfrage", "vertrag",
})
_COMPANY_STOP_TAIL = frozenset({
    "hat", "haben", "hatte", "hätte", "ist", "sind", "war", "wäre", "wird", "werden", "wurde",
    "kann", "können", "könnte", "muss", "müssen", "soll", "sollen", "sollte", "will", "wollen",
    "möchte", "möchten", "darf", "dürfen", "bittet", "bat", "meldet", "meldete", "ruft", "rief",
    "schrieb", "sagte", "teilt", "teilte", "fragt", "fragte", "bestätigt", "storniert", "kommt",
    "fährt", "stellt", "liefert", "übernimmt", "bezahlt", "überweist", "benötigt", "braucht",
    "wegen", "für", "mit", "bei", "beim", "am", "im", "in", "zu", "zum", "zur", "nach", "aus",
    "ab", "bis", "auf", "über", "unter", "neben", "zwischen", "durch", "gegen", "ohne", "um",
    "und", "oder", "aber", "denn", "weil", "dass", "daß", "wenn", "falls", "ob", "sowie", "bzw",
    "die", "der", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
    "nicht", "noch", "bereits", "schon", "hier", "dort", "heute", "morgen", "gestern", "sofort",
    "bitte", "danke", "leider", "gerne", "gern", "sehr", "auch", "nochmal", "wieder", "aktuell",
    "fahrzeuge", "fahrzeug", "lkw", "pkw", "termin", "datum", "uhr", "adresse", "anschrift",
    "telefon", "tel", "email", "e-mail", "mail", "iban", "betrag", "kosten", "preis", "rechnung",
})
_EXPLICIT_COMPANY_TRIGGERS = (
    "firma", "fa.", "unternehmen", "spedition", "umzugsfirma", "umzugsunternehmen",
    "transportfirma", "baufirma", "bauunternehmen", "reinigungsfirma", "hausverwaltung",
    "kanzlei", "praxis", "autohaus", "hotel", "agentur", "studio", "werkstatt",
    "schreinerei", "tischlerei", "malerbetrieb", "elektrobetrieb", "subunternehmer",
    "dienstleister", "partnerfirma", "wir sind von", "ich bin von", "im auftrag",
    "arbeitet bei", "angestellt bei", "beschäftigt bei", "vertrag mit",
)
_ADDRESS_BLOCK_PROSE_WORDS = frozenset({
    "ist", "sind", "war", "waren", "wäre", "wird", "werden", "wurde", "hat", "haben", "hatte",
    "kann", "können", "soll", "sollen", "muss", "müssen", "möchte", "möchten", "will", "wollen",
    "bitte", "bitten", "erbitte", "danke", "leider", "gerne", "gern", "vielen", "sehr", "liebe",
    "lieber", "hallo", "guten", "moin", "servus", "dass", "daß", "weil", "wenn", "aber", "oder",
    "nicht", "noch", "schon", "bereits", "hier", "dort", "heute", "morgen", "gestern", "kommt",
    "kommen", "fährt", "fahren", "steht", "stehen", "liegt", "liegen", "bleibt", "bleiben",
    "weitergeleitet", "weitergeleitete", "betreff", "datum", "uhrzeit", "telefon", "mobil",
    "email", "e-mail", "mail", "iban", "bic", "betrag", "kosten", "rechnung", "bestellung",
    "auftrag", "genehmigung", "adresse", "anschrift", "lieferadresse", "rechnungsadresse",
    "einsatzort", "aufstellort", "standort", "abholort", "zielort",
})

_DOMAIN_EXTRACT_RE = re.compile(r"(?:@|https?://(?:www\.)?|\bwww\.)([a-zA-Z0-9][a-zA-Z0-9\-]{2,40})\.[a-zA-Z]{2,15}\b", re.IGNORECASE)
_FIRST_WORD_WS_RE = re.compile(r"^(\S+)([ \t]+)")
_TAIL_PUNCT_SPLIT_RE = re.compile(r"(?:\.[ \t]+|[;!?()\[\]]|,[ \t]+)")
_NOT_COMPANY_VAL_RE = re.compile(r"[@]|^\d[\d\s.,:/\-]+$")
_DOMAIN_CANDIDATE_RE = re.compile(r"\b([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9\-]{2,30}(?:[ \t]+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9\-]{2,25}){0,2})\b")
_COMPACT_WS_DASH_RE = re.compile(r"[\s\-]+")
_ADDR_HEADER_BAD_RE = re.compile(r"[!?]|\.[ \t]+[A-ZÄÖÜ]|@|https?://|www\.|\d{4,}|\d{1,2}\.\d{1,2}\.\d{2,4}", re.IGNORECASE)
_HONORIFIC_HEADS = frozenset({"herr", "herrn", "frau", "hr", "fr", "dr", "prof", "c/o", "z.hd", "z.hd."})
_CONNECTOR_WORDS = frozenset({"&", "für", "von", "der", "des", "am", "im", "in", "an", "e.k.", "e.v.", "mbh", "gmbh"})
_TRAILING_CONNECTORS = frozenset({"&", "und", "für", "von", "der", "des", "am", "im", "in", "an"})


def _trim_company_prefix(raw: str) -> tuple[int, str]:
    offset = 0
    s = raw
    while True:
        m = _FIRST_WORD_WS_RE.match(s)
        if not m:
            break
        first = m.group(1)
        rest = s[m.end():]
        if not rest:
            break
        first_l = first.lower().strip(".,:;!?\"'()")
        if first_l in _COMPANY_DROP_PREFIX or (first[0].islower() and first_l not in {"van", "von", "de"}):
            offset += m.end()
            s = rest
        else:
            break
    return offset, s


def _trim_company_context_tail(raw: str) -> str:
    line = raw.splitlines()[0].strip()
    line = _TAIL_PUNCT_SPLIT_RE.split(line, maxsplit=1)[0].strip(" .,:;-")
    words = line.split()
    kept: list[str] = []
    for idx, w in enumerate(words):
        wl = w.lower().rstrip(".,:;!?")
        if idx > 0 and wl in _COMPANY_STOP_TAIL:
            break
        if idx == 0 and wl in _COMPANY_DROP_PREFIX:
            return ""
        if idx > 0 and w[0].islower() and wl not in _CONNECTOR_WORDS:
            break
        kept.append(w)
    while len(kept) > 1 and kept[-1].lower() in _TRAILING_CONNECTORS:
        kept.pop()
    return " ".join(kept).strip(" .,:;-")


def _extract_company_domain_stems(text: str) -> set[str]:
    if "@" not in text and "www." not in text.lower() and "http" not in text.lower():
        return set()
    stems: set[str] = set()
    for m in _DOMAIN_EXTRACT_RE.finditer(text):
        stem = m.group(1).lower()
        if stem in FREEMAIL_DOMAIN_STEMS or len(stem) < 4:
            continue
        stems.add(stem)
        for part in stem.split("-"):
            if len(part) >= 5 and part not in FREEMAIL_DOMAIN_STEMS and part not in MAJOR_GERMAN_CITIES and part not in NON_NAME_WORDS:
                stems.add(part)
    return stems


def _valid_company(candidate: str) -> bool:
    if not candidate or len(candidate) < 2:
        return False
    low = candidate.lower()
    if is_public_authority(candidate):
        return False
    if low in NON_NAME_WORDS or low in MAJOR_GERMAN_CITIES or low in ORG_NOISE_WORDS:
        return False
    if IMAGE_OR_SOCIAL_LINE_RE.match(candidate):
        return False
    return True


def _is_standalone_street_line(line_txt: str) -> bool:
    s = line_txt.strip()
    if not s or len(s) > 95:
        return False
    m_st = STREET_COMPOUND_RE.match(s) or STREET_PREFIX_RE.match(s)
    if not m_st or not is_plausible_street(m_st.group(0).strip()):
        return False
    tail = s[m_st.end():].strip(" ,;-/")
    return len(tail.split()) <= 5


def _is_person_or_role_header(cand: str) -> bool:
    words = cand.split()
    if not words:
        return False
    first_l = words[0].lower().rstrip(".,:")
    if first_l in _HONORIFIC_HEADS:
        return True
    if "&" in cand or first_l in {"der", "die", "das"} or _LEGAL_FORM_FULL_RE.search(cand) or _COMPANY_INDUSTRY_WORD_RE.search(cand):
        return False
    if any(matches_job_stem(normalize_job_token(w)) for w in words):
        return True
    if 2 <= len(words) <= 3:
        w0 = words[0].lower().rstrip(".,")
        w_last = words[-1].lower().rstrip(".,")
        if w0 in COMMON_FIRST_NAMES or (len(words) == 2 and w_last in COMMON_SURNAMES):
            return True
    return False


def _is_plausible_address_block_company(cand: str) -> bool:
    if not cand or len(cand) < 2 or len(cand) > 65:
        return False
    if not (cand[0].isupper() or cand[0].isdigit()) or cand[-1] in ":?!,;":
        return False
    if _ADDR_HEADER_BAD_RE.search(cand):
        return False
    if _SIGNOFF_LINE_RE.match(cand) or _SALUTATION_RE.match(cand) or IMAGE_OR_SOCIAL_LINE_RE.match(cand):
        return False
    if not _valid_company(cand):
        return False
    words = cand.split()
    if not (1 <= len(words) <= 6):
        return False
    low_words = [w.lower().strip(".,:;!?\"'()") for w in words]
    if any(lw in _ADDRESS_BLOCK_PROSE_WORDS for lw in low_words):
        return False
    return not all(lw in NON_NAME_WORDS or lw in MAJOR_GERMAN_CITIES for lw in low_words)


def detect_companies(text: str, *, lines_cache: list[tuple[int, int, str]] | None = None) -> list[SpanMatch]:
    matches: list[SpanMatch] = []

    # 1. Rechtsform (GmbH, UG, AG, ...)
    for m in _COMPANY_LEGAL_RE.finditer(text):
        rel_off, cleaned = _trim_company_prefix(m.group(1))
        cleaned = cleaned.strip(" .,:;")
        if _valid_company(cleaned):
            start = m.start(1) + rel_off
            matches.append(SpanMatch(start, start + len(cleaned), cleaned, Category.COMPANY, Confidence.HIGH, "Firmenname (Rechtsform)"))

    # 2. Feld-Labels („Firma: ...“, „Arbeitgeber: ...“)
    if ":" in text:
        for m in _COMPANY_LABEL_RE.finditer(text):
            raw = m.group(1)
            lead = len(raw) - len(raw.lstrip(" \t"))
            cleaned = _trim_company_context_tail(raw.lstrip(" \t"))
            if _valid_company(cleaned) and not _NOT_COMPANY_VAL_RE.search(cleaned):
                start = m.start(1) + lead
                matches.append(SpanMatch(start, start + len(cleaned), cleaned, Category.COMPANY, Confidence.HIGH, "Firmenname (Feld-Label)"))

    # 3. Kontext-Sätze
    for m in _COMPANY_CONTEXT_RE.finditer(text):
        cleaned = _trim_company_context_tail(m.group(1))
        if not _valid_company(cleaned):
            continue
        if cleaned.split()[0].lower() in {"herr", "herrn", "frau", "hr.", "fr.", "dr.", "prof."}:
            continue
        prefix_txt = text[m.start():m.start(1)].lower()
        is_explicit = any(kw in prefix_txt for kw in _EXPLICIT_COMPANY_TRIGGERS)
        if not is_explicit:
            if not (_LEGAL_FORM_FULL_RE.search(cleaned) or "&" in cleaned or _COMPANY_INDUSTRY_WORD_RE.search(cleaned)):
                continue
        matches.append(SpanMatch(m.start(1), m.start(1) + len(cleaned), cleaned, Category.COMPANY, Confidence.HIGH, "Firmenname (Kontext)"))

    # 4. „& Söhne / & Partner / & Co.“
    if "&" in text:
        for m in _COMPANY_AMP_RE.finditer(text):
            cleaned = m.group(1).strip(" .,:;")
            if _valid_company(cleaned):
                matches.append(SpanMatch(m.start(1), m.start(1) + len(cleaned), cleaned, Category.COMPANY, Confidence.HIGH, "Firmenname (& Partner / & Söhne)"))

    # 5. Markenname + Branchenwort
    for m in _COMPANY_INDUSTRY_PHRASE_RE.finditer(text):
        rel_off, cleaned = _trim_company_prefix(m.group(1))
        cleaned = cleaned.strip(" .,:;")
        if not _valid_company(cleaned) or len(cleaned.split()) < 2:
            continue
        w0_l = cleaned.split()[0].lower()
        if w0_l in NON_NAME_WORDS and w0_l not in COMMON_SURNAMES:
            continue
        start = m.start(1) + rel_off
        matches.append(SpanMatch(start, start + len(cleaned), cleaned, Category.COMPANY, Confidence.HIGH, "Firmenname (Branchenbezeichnung)"))

    # 6. Domain-Kontext-Abgleich
    domain_stems = _extract_company_domain_stems(text)
    if domain_stems:
        url_or_mail_ranges = [
            (um.start(), um.end())
            for um in list(EMAIL_RE.finditer(text)) + list(URL_FULL_RE.finditer(text)) + list(BARE_DOMAIN_RE.finditer(text))
        ]
        for cand_m in _DOMAIN_CANDIDATE_RE.finditer(text):
            cs, ce = cand_m.start(1), cand_m.end(1)
            if any(us <= cs and ce <= ue for us, ue in url_or_mail_ranges):
                continue
            cand = cand_m.group(1)
            norm_compact = _COMPACT_WS_DASH_RE.sub("", cand.lower())
            norm_ascii = norm_compact.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
            if (norm_compact in domain_stems or norm_ascii in domain_stems) and _valid_company(cand):
                matches.append(SpanMatch(cs, ce, cand, Category.COMPANY, Confidence.HIGH, "Firmenname (E-Mail-/Web-Domain-Kontext)"))

    # 7. Adressblock-Kontext (Zeile direkt über alleinstehender Straßenzeile, z. B. „Der Packesel\nAlte Heroldstr.23“)
    if "\n" in text:
        line_records = lines_cache if lines_cache is not None else iter_lines_with_offsets(text)
        existing_comp_spans = [(m.start, m.end) for m in matches]
        for idx, (_ls, _le, line_txt) in enumerate(line_records):
            if idx == 0 or not _is_standalone_street_line(line_txt):
                continue
            for lookback in (idx - 1, idx - 2):
                if lookback < 0:
                    break
                prev_start, _prev_end, prev_txt = line_records[lookback]
                cand = prev_txt.strip()
                if not cand:
                    break
                if _is_person_or_role_header(cand):
                    continue
                if _is_plausible_address_block_company(cand):
                    lead = len(prev_txt) - len(prev_txt.lstrip(" \t"))
                    cs = prev_start + lead
                    ce = cs + len(cand)
                    if not any(es <= cs and ce <= ee for es, ee in existing_comp_spans):
                        matches.append(SpanMatch(cs, ce, cand, Category.COMPANY, Confidence.REVIEW, "Firmenname (Adressblock über Straße)"))
                        existing_comp_spans.append((cs, ce))
                break

    return matches
