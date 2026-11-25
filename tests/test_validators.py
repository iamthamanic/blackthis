"""Unit-Tests für mathematische & strukturelle Validatoren."""

from dsgvo_redactor.validators import (
    validate_german_kfz,
    validate_german_plz,
    validate_german_tax_id,
    validate_iban,
    validate_luhn,
)


def test_iban_validation():
    assert validate_iban("DE89 3704 0044 0532 0130 00") is True
    assert validate_iban("DE89370400440532013000") is True
    assert validate_iban("AT61 1904 3002 3457 3201") is True
    assert validate_iban("CH93 0076 2011 6238 5295 7") is True
    # Ungültige Prüfziffer
    assert validate_iban("DE89 3704 0044 0532 0130 01") is False
    # Falsche Länge
    assert validate_iban("DE89 3704 0044") is False


def test_luhn_validation():
    assert validate_luhn("4532 0151 1283 0366") is True
    assert validate_luhn("4532-0151-1283-0367") is False
    assert validate_luhn("123456") is False


def test_german_tax_id():
    # Bekannte gültige Test-Steuer-IDs (synthetisch, valide Prüfziffer nach ISO 7064 Mod 11, 10)
    assert validate_german_tax_id("86095742719") is True
    assert validate_german_tax_id("86 095 742 719") is True
    assert validate_german_tax_id("86095742718") is False
    assert validate_german_tax_id("01234567890") is False


def test_german_plz():
    assert validate_german_plz("50935") is True
    assert validate_german_plz("01067") is True
    assert validate_german_plz("00123") is False
    assert validate_german_plz("1234") is False


def test_german_kfz():
    assert validate_german_kfz("K", "AB", "4821") is True
    assert validate_german_kfz("B", "XY", "99") is True
    assert validate_german_kfz("M", "CD", "123") is True
    assert validate_german_kfz("ZZZ", "AB", "123") is False
    assert validate_german_kfz("HV", "12", "396094") is False
