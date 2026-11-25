"""Schnelle mathematische und strukturelle Validatoren für deutsche & internationale Kennungen."""

from __future__ import annotations

import re

# Offizielle IBAN-Längen pro Länderkürzel (SEPA + häufige internationale Länder)
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22,
    "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24,
    "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26, "UA": 29,
    "VA": 22, "VG": 24, "XK": 20,
}

_STRIP_SEP_TRANS = str.maketrans("", "", " \t\r\n-")
_STRIP_TAX_TRANS = str.maketrans("", "", " \t\r\n-/")
_IBAN_SHAPE_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")
_VALID_CARD_FIRST = frozenset("23456")


def validate_iban(raw: str) -> bool:
    """Prüft eine IBAN exakt nach ISO 13616 (Länge + Modulo-97-Prüfziffer)."""
    cleaned = raw.translate(_STRIP_SEP_TRANS).upper()
    n = len(cleaned)
    if n < 15 or n > 34:
        return False
    expected = IBAN_LENGTHS.get(cleaned[:2])
    if expected is None or n != expected:
        return False
    if not _IBAN_SHAPE_RE.match(cleaned):
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    # Streaming Mod-97 ohne große String-Konkatenation
    rem = 0
    for ch in rearranged:
        code = ord(ch)
        if 48 <= code <= 57:
            rem = (rem * 10 + (code - 48)) % 97
        else:
            val = code - 55
            rem = (rem * 100 + val) % 97
    return rem == 1


def validate_luhn(raw: str) -> bool:
    """Luhn-Prüfziffer für Kreditkartennummern (13–19 Ziffern)."""
    digits = raw.translate(_STRIP_SEP_TRANS)
    n = len(digits)
    if not (13 <= n <= 19) or not digits.isascii() or not digits.isdigit():
        return False
    if digits[0] not in _VALID_CARD_FIRST:
        return False
    total = 0
    double = False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return total % 10 == 0


def validate_german_tax_id(raw: str) -> bool:
    """Prüft eine 11-stellige deutsche Steuer-Identifikationsnummer (IdNr nach § 139b AO)."""
    digits = raw.translate(_STRIP_TAX_TRANS)
    if len(digits) != 11 or digits[0] == "0" or not digits.isascii() or not digits.isdigit():
        return False
    first_ten = digits[:10]
    counts: dict[str, int] = {}
    for ch in first_ten:
        counts[ch] = counts.get(ch, 0) + 1
    max_freq = max(counts.values())
    if max_freq == 2:
        if sum(1 for v in counts.values() if v == 2) != 1:
            return False
    elif max_freq == 3:
        if sum(1 for v in counts.values() if v == 3) != 1:
            return False
    else:
        return False

    product = 10
    for ch in first_ten:
        s = ((ord(ch) - 48) + product) % 10
        if s == 0:
            s = 10
        product = (2 * s) % 11
    check = 11 - product
    if check == 10:
        check = 0
    return check == (ord(digits[10]) - 48)


def validate_german_plz(raw: str) -> bool:
    """Prüft, ob eine 5-stellige Zahl im gültigen deutschen PLZ-Bereich liegt (01001–99998)."""
    if len(raw) != 5 or not raw.isascii() or not raw.isdigit():
        return False
    if raw[0] == "0" and raw[1] == "0":
        return False
    num = int(raw)
    return 1001 <= num <= 99998


# Umfassende Liste gültiger deutscher Kfz-Unterscheidungszeichen (1-3 Buchstaben)
GERMAN_KFZ_DISTRICTS: frozenset[str] = frozenset({
    "A", "AA", "AB", "ABG", "ABI", "AC", "AE", "AH", "AIB", "AIC", "AK", "ALF", "ALZ", "AM", "AN",
    "ANA", "ANG", "ANK", "AÖ", "AP", "APD", "ARN", "ART", "AS", "ASL", "ASZ", "AT", "AU", "AUR",
    "AW", "AZ", "AZE",
    "B", "BA", "BAD", "BAR", "BB", "BBG", "BBL", "BC", "BCH", "BD", "BE", "BED", "BER", "BF", "BGD",
    "BGL", "BH", "BI", "BID", "BIN", "BIR", "BIT", "BIW", "BK", "BKS", "BL", "BLB", "BLK", "BM",
    "BN", "BNA", "BO", "BÖ", "BOG", "BOH", "BOR", "BOT", "BP", "BRA", "BRB", "BRG", "BRK", "BRL",
    "BRV", "BS", "BSB", "BSK", "BT", "BTF", "BÜD", "BUL", "BÜR", "BÜS", "BÜZ", "BW", "BWL", "BYL",
    "BZ",
    "C", "CA", "CAS", "CB", "CE", "CHA", "CLP", "CLZ", "CO", "COC", "COE", "CR", "CUX", "CW",
    "D", "DA", "DAH", "DAN", "DAU", "DBR", "DD", "DE", "DEG", "DEL", "DGF", "DH", "DI", "DIL",
    "DIN", "DIZ", "DKB", "DL", "DLG", "DM", "DN", "DO", "DON", "DU", "DUD", "DÜW", "DW", "DZ",
    "E", "EA", "EB", "EBE", "EBN", "EBS", "ECK", "ED", "EE", "EF", "EG", "EH", "EI", "EIC", "EIL",
    "EIN", "EIS", "EL", "EM", "EMD", "EMS", "EN", "ER", "ERB", "ERH", "ERK", "ERZ", "ES", "ESB",
    "ESW", "EU", "EW",
    "F", "FB", "FD", "FDB", "FDS", "FEU", "FF", "FFB", "FG", "FI", "FKB", "FL", "FLÖ", "FN", "FO",
    "FOR", "FR", "FRG", "FRI", "FRW", "FS", "FT", "FTL", "FÜ", "FÜS", "FW", "FZ",
    "G", "GA", "GAN", "GAP", "GC", "GD", "GDB", "GE", "GEL", "GEO", "GER", "GF", "GG", "GHA", "GHC",
    "GI", "GK", "GL", "GLA", "GM", "GMN", "GN", "GNT", "GÖ", "GOA", "GOH", "GP", "GR", "GRA", "GRH",
    "GRI", "GRM", "GRZ", "GS", "GT", "GTH", "GÜ", "GUB", "GUN", "GV", "GVM", "GW", "GZ",
    "H", "HA", "HAB", "HAL", "HAM", "HAS", "HB", "HBN", "HBS", "HC", "HCH", "HD", "HDH", "HDL",
    "HE", "HEB", "HEF", "HEI", "HEL", "HER", "HET", "HF", "HG", "HGN", "HGW", "HH", "HHM", "HI",
    "HIG", "HIP", "HK", "HL", "HM", "HMÜ", "HN", "HO", "HOG", "HOH", "HOL", "HOM", "HOR", "HÖS",
    "HOT", "HP", "HR", "HRO", "HS", "HSK", "HST", "HU", "HV", "HVL", "HWI", "HX", "HY", "HZ",
    "IGB", "IK", "IL", "ILL", "IN", "IZ",
    "J", "JE", "JL", "JÜL",
    "K", "KA", "KB", "KC", "KE", "KEH", "KEL", "KEM", "KF", "KG", "KH", "KI", "KIB", "KK", "KL",
    "KLE", "KLZ", "KM", "KN", "KO", "KÖN", "KÖT", "KÖZ", "KR", "KRU", "KS", "KT", "KU", "KÜN",
    "KUS", "KW", "KY", "KYF",
    "L", "LA", "LAN", "LAU", "LB", "LBS", "LBZ", "LC", "LD", "LDK", "LDS", "LEO", "LER", "LEV",
    "LF", "LG", "LH", "LI", "LIB", "LIF", "LIP", "LL", "LM", "LN", "LÖ", "LÖB", "LOS", "LP", "LR",
    "LRO", "LSA", "LSN", "LSZ", "LU", "LÜN", "LUP", "LWL",
    "M", "MA", "MAB", "MAI", "MAK", "MAL", "MB", "MC", "MD", "ME", "MED", "MEG", "MEI", "MEK",
    "MEL", "MER", "MET", "MG", "MGH", "MGN", "MH", "MHL", "MI", "MIL", "MK", "MKK", "ML", "MM",
    "MN", "MO", "MOD", "MOL", "MON", "MOS", "MQ", "MR", "MS", "MSE", "MSH", "MSP", "MST", "MTK",
    "MTL", "MÜ", "MÜB", "MÜR", "MVL", "MW", "MY", "MYK", "MZ", "MZG",
    "N", "NAB", "NAI", "NAU", "NB", "ND", "NDH", "NE", "NEA", "NEB", "NEC", "NEN", "NES", "NEW",
    "NF", "NH", "NI", "NK", "NL", "NM", "NMB", "NMS", "NÖ", "NOH", "NOL", "NOM", "NOR", "NP", "NR",
    "NRW", "NT", "NU", "NVP", "NW", "NWM", "NY", "NZ",
    "OA", "OAL", "OB", "OBB", "OBG", "OC", "OCH", "OD", "OE", "OF", "OG", "OH", "OHA", "ÖHR", "OHV",
    "OHZ", "OK", "OL", "OP", "OPR", "OS", "OSL", "OVI", "OVL", "OZ",
    "P", "PA", "PAF", "PAN", "PAR", "PB", "PCH", "PE", "PEG", "PF", "PI", "PIR", "PL", "PLÖ", "PM",
    "PN", "PR", "PRÜ", "PS", "PW", "PZ",
    "QFT", "QLB",
    "R", "RA", "RC", "RD", "RDG", "RE", "REG", "REH", "REI", "RG", "RH", "RI", "RID", "RIE", "RL",
    "RM", "RN", "RO", "ROD", "ROF", "ROK", "ROL", "ROS", "ROT", "ROW", "RP", "RPL", "RS", "RSL",
    "RT", "RU", "RÜD", "RÜG", "RV", "RW", "RZ",
    "S", "SAB", "SAD", "SAL", "SAN", "SAW", "SB", "SBG", "SBK", "SC", "SCZ", "SDH", "SDL", "SDT",
    "SE", "SEB", "SEE", "SEF", "SEL", "SFB", "SFT", "SG", "SGH", "SH", "SHA", "SHG", "SHK", "SHL",
    "SI", "SIG", "SIM", "SK", "SL", "SLE", "SLF", "SLK", "SLN", "SLS", "SLÜ", "SLZ", "SM", "SMÜ",
    "SN", "SO", "SOB", "SOG", "SOK", "SÖM", "SON", "SP", "SPB", "SPN", "SR", "SRB", "SRO", "ST",
    "STA", "STB", "STD", "STE", "STL", "SU", "SUL", "SÜW", "SW", "SWA", "SZ", "SZB",
    "TBB", "TDO", "TE", "TET", "TF", "TG", "THL", "THW", "TIR", "TO", "TÖL", "TP", "TR", "TS",
    "TT", "TÜ", "TUT",
    "UE", "UEM", "UFF", "UH", "UL", "UM", "UN", "USI", "ÜB",
    "V", "VAI", "VB", "VEC", "VER", "VG", "VIB", "VIE", "VIT", "VK", "VOH", "VR", "VS",
    "W", "WA", "WAF", "WAK", "WAN", "WAT", "WB", "WBS", "WDA", "WE", "WEL", "WEN", "WER", "WES",
    "WF", "WG", "WHV", "WI", "WIL", "WIS", "WIT", "WIZ", "WK", "WL", "WLG", "WM", "WMS", "WN",
    "WND", "WO", "WOB", "WOH", "WOL", "WOR", "WOS", "WR", "WRN", "WS", "WSF", "WST", "WSW", "WT",
    "WTL", "WTM", "WÜ", "WUG", "WÜM", "WUN", "WUR", "WW", "WZ", "WZL",
    "X", "Y", "Z", "ZE", "ZEL", "ZI", "ZIG", "ZP", "ZR", "ZW", "ZZ",
})


def validate_german_kfz(district: str, letters: str, digits: str) -> bool:
    """Validiert ein deutsches Kfz-Kennzeichen (Unterscheidungszeichen + 1-2 Buchstaben + 1-4 Ziffern)."""
    d = district.upper()
    if d not in GERMAN_KFZ_DISTRICTS:
        return False
    if not (1 <= len(letters) <= 2) or not letters.isascii() or not letters.isalpha():
        return False
    if not (1 <= len(digits) <= 4) or digits[0] == "0" or not digits.isascii() or not digits.isdigit():
        return False
    return len(d) + len(letters) + len(digits) <= 8
