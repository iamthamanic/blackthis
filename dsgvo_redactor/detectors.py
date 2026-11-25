"""Zentrale Fassade & schneller Orchestrator für alle modularen Regel-Detektoren."""

from __future__ import annotations

from .detectors_addresses import detect_addresses_plz_housenumbers, detect_locations
from .detectors_common import has_digit, iter_lines_with_offsets
from .detectors_entities import detect_companies, detect_job_titles, detect_names
from .detectors_structured import (
    detect_amounts,
    detect_bank,
    detect_birthdates,
    detect_custom_blacklist,
    detect_emails,
    detect_ip_addresses,
    detect_json_kv_fields,
    detect_license_plates,
    detect_phones,
    detect_tax_ids,
    detect_websites,
)
from .models import Category, RedactionConfig, SpanMatch

__all__ = [
    "run_rule_detectors",
    "detect_addresses_plz_housenumbers",
    "detect_amounts",
    "detect_bank",
    "detect_birthdates",
    "detect_companies",
    "detect_custom_blacklist",
    "detect_emails",
    "detect_ip_addresses",
    "detect_job_titles",
    "detect_json_kv_fields",
    "detect_license_plates",
    "detect_locations",
    "detect_names",
    "detect_phones",
    "detect_tax_ids",
    "detect_websites",
]


def run_rule_detectors(text: str, config: RedactionConfig) -> list[SpanMatch]:
    """Führt alle aktivierten deterministischen & kontextbasierten Detektoren mit Fast-Path-Vorchecks aus."""
    if not text or not text.strip():
        return []

    enabled = config.enabled_categories()
    all_matches: list[SpanMatch] = []

    # Einmalig pro Zelle berechnen und an alle Sub-Detektoren durchreichen
    text_has_digit = has_digit(text)
    lines_cache = iter_lines_with_offsets(text) if "\n" in text and (Category.NAME in enabled or Category.COMPANY in enabled) else None

    if config.extra_blacklist:
        all_matches.extend(detect_custom_blacklist(text, config.extra_blacklist))

    all_matches.extend(detect_json_kv_fields(text, enabled))

    email_matches: list[SpanMatch] = []
    if Category.EMAIL in enabled:
        email_matches = detect_emails(text)
        all_matches.extend(email_matches)

    if Category.WEBSITE in enabled:
        email_spans = [(m.start, m.end) for m in email_matches]
        all_matches.extend(detect_websites(text, email_spans))

    if Category.IP_ADDRESS in enabled and text_has_digit:
        all_matches.extend(detect_ip_addresses(text, text_has_digit=True))

    if Category.BANK in enabled:
        all_matches.extend(detect_bank(text, text_has_digit=text_has_digit))

    if Category.TAX_ID in enabled and text_has_digit:
        all_matches.extend(detect_tax_ids(text, text_has_digit=True))

    if Category.PHONE in enabled and text_has_digit:
        all_matches.extend(detect_phones(text, text_has_digit=True))

    if Category.BIRTHDATE in enabled and text_has_digit:
        all_matches.extend(detect_birthdates(text, text_has_digit=True))

    if Category.LICENSE_PLATE in enabled and text_has_digit:
        all_matches.extend(detect_license_plates(text, text_has_digit=True))

    if Category.AMOUNT in enabled and text_has_digit:
        all_matches.extend(detect_amounts(text, text_has_digit=True))

    if any(c in enabled for c in (Category.ADDRESS, Category.HOUSE_NUMBER, Category.POSTAL_CODE)):
        all_matches.extend(
            detect_addresses_plz_housenumbers(
                text,
                want_address=Category.ADDRESS in enabled,
                want_house_number=Category.HOUSE_NUMBER in enabled,
                want_plz=Category.POSTAL_CODE in enabled,
                text_has_digit=text_has_digit,
            )
        )

    if Category.LOCATION in enabled:
        all_matches.extend(detect_locations(text))

    if Category.JOB_TITLE in enabled:
        all_matches.extend(detect_job_titles(text))

    if Category.NAME in enabled:
        all_matches.extend(detect_names(text, lines_cache=lines_cache))

    if Category.COMPANY in enabled:
        all_matches.extend(detect_companies(text, lines_cache=lines_cache))

    return all_matches
