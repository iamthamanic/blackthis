"""Datenmodelle für DSGVO CSV Redactor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Category(str, Enum):
    """Kategorien sensibler personenbezogener Daten."""

    NAME = "NAME"
    ADDRESS = "ADRESSE"
    HOUSE_NUMBER = "HAUSNUMMER"
    POSTAL_CODE = "PLZ"
    LOCATION = "ORTSCHAFT"
    BANK = "KONTODATEN"
    JOB_TITLE = "BERUF_POSITION"
    AMOUNT = "GELDBETRAG"
    PHONE = "TELEFON"
    EMAIL = "EMAIL"
    WEBSITE = "WEBSEITE"
    IP_ADDRESS = "IP_ADRESSE"
    BIRTHDATE = "GEBURTSDATUM"
    LICENSE_PLATE = "KFZ_KENNZEICHEN"
    TAX_ID = "STEUER_AUSWEIS_ID"
    COMPANY = "FIRMENNAME"
    MANUAL = "MANUELL"


CATEGORY_LABELS_DE: dict[Category, str] = {
    Category.NAME: "Klarname",
    Category.ADDRESS: "Adresse / Straße",
    Category.HOUSE_NUMBER: "Hausnummer",
    Category.POSTAL_CODE: "Postleitzahl",
    Category.LOCATION: "Ortschaft / Stadt",
    Category.BANK: "Kontodaten",
    Category.JOB_TITLE: "Job-Position / Beruf",
    Category.AMOUNT: "Geldbetrag",
    Category.PHONE: "Telefonnummer",
    Category.EMAIL: "E-Mail-Adresse",
    Category.WEBSITE: "Webseite / URL",
    Category.IP_ADDRESS: "IP-Adresse",
    Category.BIRTHDATE: "Geburtsdatum",
    Category.LICENSE_PLATE: "Kfz-Kennzeichen",
    Category.TAX_ID: "Steuer- / Ausweis-ID",
    Category.COMPANY: "Firmenname",
    Category.MANUAL: "Manuelle Regel überall",
}

# Priorität bei überlappenden Fundstellen (höher gewinnt)
CATEGORY_PRIORITY: dict[Category, int] = {
    Category.BANK: 100,
    Category.EMAIL: 98,
    Category.TAX_ID: 95,
    Category.IP_ADDRESS: 93,
    Category.WEBSITE: 92,
    Category.PHONE: 90,
    Category.BIRTHDATE: 88,
    Category.LICENSE_PLATE: 86,
    Category.ADDRESS: 85,
    Category.POSTAL_CODE: 82,
    Category.HOUSE_NUMBER: 80,
    Category.AMOUNT: 78,
    Category.JOB_TITLE: 75,
    Category.COMPANY: 74,
    Category.NAME: 72,
    Category.LOCATION: 70,
    Category.MANUAL: 110,
}


class Confidence(str, Enum):
    """Konfidenzstufe eines Treffers."""

    HIGH = "HIGH"        # Sicher erkannt
    REVIEW = "REVIEW"    # Unsicher / Grenzfall – im Zweifel geschwärzt, gelb markiert


@dataclass
class SpanMatch:
    """Ein einzelner erkannter Textabschnitt innerhalb einer Zelle."""

    start: int
    end: int
    text: str
    category: Category
    confidence: Confidence
    rule: str
    reasons: list[str] = field(default_factory=list)
    redacted: bool = True
    protected: bool = False
    match_id: str = ""

    def __post_init__(self) -> None:
        if not self.reasons:
            self.reasons = [self.rule]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.match_id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "category": self.category.value,
            "category_label": CATEGORY_LABELS_DE.get(self.category, self.category.value),
            "confidence": self.confidence.value,
            "rule": self.rule,
            "reasons": self.reasons,
            "redacted": self.redacted,
            "protected": self.protected,
        }


@dataclass
class RedactionConfig:
    """Konfiguration für einen Schwärzungsdurchlauf."""

    applicant_first_name: str = ""
    applicant_last_name: str = ""
    extra_whitelist: list[str] = field(default_factory=list)
    extra_blacklist: list[str] = field(default_factory=list)
    replacement: str = "██████"
    use_typed_placeholders: bool = False

    # Kategorien (Standard gemäß Anforderungen)
    redact_names: bool = True
    redact_addresses: bool = True
    redact_house_numbers: bool = True
    redact_postal_codes: bool = True
    redact_locations: bool = True
    redact_bank: bool = True
    redact_job_titles: bool = True
    redact_amounts: bool = True
    redact_phones: bool = True
    redact_emails: bool = True
    redact_websites: bool = True
    redact_ip_addresses: bool = True
    redact_birthdates: bool = True
    redact_license_plates: bool = True
    redact_tax_ids: bool = True
    redact_companies: bool = False  # Standardmäßig AUS, per Checkbox aktivierbar

    # DSGVO-Sicherheit: Unsichere Treffer im Zweifel automatisch schwärzen
    redact_uncertain_by_default: bool = True
    use_ner: bool = True
    columns: list[str] | None = None

    def enabled_categories(self) -> set[Category]:
        enabled: set[Category] = {Category.MANUAL}
        mapping = {
            Category.NAME: self.redact_names,
            Category.ADDRESS: self.redact_addresses,
            Category.HOUSE_NUMBER: self.redact_house_numbers,
            Category.POSTAL_CODE: self.redact_postal_codes,
            Category.LOCATION: self.redact_locations,
            Category.BANK: self.redact_bank,
            Category.JOB_TITLE: self.redact_job_titles,
            Category.AMOUNT: self.redact_amounts,
            Category.PHONE: self.redact_phones,
            Category.EMAIL: self.redact_emails,
            Category.WEBSITE: self.redact_websites,
            Category.IP_ADDRESS: self.redact_ip_addresses,
            Category.BIRTHDATE: self.redact_birthdates,
            Category.LICENSE_PLATE: self.redact_license_plates,
            Category.TAX_ID: self.redact_tax_ids,
            Category.COMPANY: self.redact_companies,
        }
        for cat, is_on in mapping.items():
            if is_on:
                enabled.add(cat)
        return enabled

    def replacement_for(self, category: Category) -> str:
        if self.use_typed_placeholders:
            return f"[{category.value}]"
        return self.replacement


@dataclass
class CellResult:
    """Ergebnis für eine einzelne gescannte CSV-Zelle."""

    row_index: int
    column: str
    original: str
    redacted: str
    matches: list[SpanMatch] = field(default_factory=list)

    @property
    def high_count(self) -> int:
        return sum(1 for m in self.matches if not m.protected and m.redacted and m.confidence == Confidence.HIGH)

    @property
    def review_count(self) -> int:
        return sum(1 for m in self.matches if not m.protected and m.redacted and m.confidence == Confidence.REVIEW)

    @property
    def protected_count(self) -> int:
        return sum(1 for m in self.matches if m.protected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "column": self.column,
            "original": self.original,
            "redacted": self.redacted,
            "matches": [m.to_dict() for m in self.matches],
            "high_count": self.high_count,
            "review_count": self.review_count,
            "protected_count": self.protected_count,
        }


@dataclass
class ScanSummary:
    """Zusammenfassung eines kompletten CSV-Scans."""

    total_rows: int
    scanned_columns: list[str]
    rows_with_findings: int
    total_redactions: int
    high_confidence_redactions: int
    review_findings: int
    protected_applicant_hits: int
    by_category: dict[str, int]
    ner_active: bool
    ner_model_name: str
    encoding: str = "utf-8"
    delimiter: str = ";"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "scanned_columns": self.scanned_columns,
            "rows_with_findings": self.rows_with_findings,
            "total_redactions": self.total_redactions,
            "high_confidence_redactions": self.high_confidence_redactions,
            "review_findings": self.review_findings,
            "protected_applicant_hits": self.protected_applicant_hits,
            "by_category": self.by_category,
            "ner_active": self.ner_active,
            "ner_model_name": self.ner_model_name,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
        }
