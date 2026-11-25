"""Tests für Engine, Überlappungsauflösung und Antragsteller-Schutz (Whitelist)."""

from dsgvo_redactor.engine import RedactionEngine
from dsgvo_redactor.models import RedactionConfig
from dsgvo_redactor.ner_engine import LocalGermanNER


def _engine() -> RedactionEngine:
    # Nutzt lokales spaCy falls installiert (wie in Produktion)
    return RedactionEngine(ner=LocalGermanNER())


def test_applicant_name_protected_in_all_forms_while_third_party_redacted():
    eng = _engine()
    cfg = RedactionConfig(
        applicant_first_name="Thomas",
        applicant_last_name="Mustermann",
        replacement="██████",
    )
    text = (
        "Sehr geehrter Herr Mustermann,\n"
        "bitte geben Sie Thomas Mustermann Bescheid (auch T. Mustermann).\n"
        "Kunde ist aber Herr Klaus Schmidt und Max Meier.\n"
        "Außerdem hat Thomas Meier angerufen."
    )
    res = eng.analyze_cell(text, cfg)
    # Antragsteller-Formen bleiben sichtbar!
    assert "Herr Mustermann" in res.redacted
    assert "Thomas Mustermann" in res.redacted
    assert "T. Mustermann" in res.redacted
    # Dritte Personen werden konsequent geschwärzt!
    assert "Klaus Schmidt" not in res.redacted
    assert "Max Meier" not in res.redacted
    # Namensvetter mit gleichem Vornamen („Thomas Meier“) darf NICHT geleakt werden!
    assert "Meier" not in res.redacted


def test_applicant_same_surname_different_first_name_redacted():
    eng = _engine()
    cfg = RedactionConfig(
        applicant_first_name="Thomas",
        applicant_last_name="Mustermann",
        replacement="██████",
    )
    text = "Kontoinhaber: Klaus Mustermann\nSachbearbeiter: Herr Mustermann"
    res = eng.analyze_cell(text, cfg)
    # „Klaus Mustermann“ ist eine ANDERE Person -> muss geschwärzt werden!
    assert "Klaus" not in res.redacted
    # „Herr Mustermann“ ist der Antragsteller -> bleibt sichtbar!
    assert "Herr Mustermann" in res.redacted
    # „Sachbearbeiter“ ist eine Berufsbezeichnung -> wird geschwärzt!
    assert "Sachbearbeiter" not in res.redacted


def test_custom_replacement_and_typed_placeholders():
    eng = _engine()
    cfg = RedactionConfig(use_typed_placeholders=True)
    text = "IBAN: DE89 3704 0044 0532 0130 00, Betrag: 99,90 €"
    res = eng.analyze_cell(text, cfg)
    assert "[KONTODATEN]" in res.redacted
    assert "[GELDBETRAG]" in res.redacted
    assert "DE89" not in res.redacted
    assert "99,90" not in res.redacted


def test_no_newline_name_merge_and_no_reference_false_positives():
    eng = _engine()
    cfg = RedactionConfig(
        applicant_first_name="Max",
        applicant_last_name="Mustermann",
        redact_companies=False,
    )
    text_company = "Mustermann, Max\nMusterlogistik GmbH\nBerlin"
    res_company = eng.analyze_cell(text_company, cfg)
    assert "Mustermann, Max" in res_company.redacted
    assert "Musterlogistik GmbH" in res_company.redacted

    text_ref = "Kundenreferenz: TEST-12345\nBestellung für Kundenreferenz: TEST-12345"
    res_ref = eng.analyze_cell(text_ref, cfg)
    assert res_ref.redacted == text_ref


def test_locations_detected_while_public_authorities_and_countries_preserved():
    eng = _engine()
    text = (
        "Bitte bei der Stadt Köln und dem Ordnungsamt Berlin einreichen.\n"
        "Umzug von Frechen nach Bad Honnef, Wohnort: Hürth-Efferen.\n"
        "Einsatzort in Pulheim und Siegburg (Deutschland)."
    )
    cfg_on = RedactionConfig(redact_locations=True)
    res_on = eng.analyze_cell(text, cfg_on)

    # Behörden & Länder bleiben sichtbar!
    assert "Stadt Köln" in res_on.redacted
    assert "Ordnungsamt Berlin" in res_on.redacted
    assert "Deutschland" in res_on.redacted

    # Ortschaften / Städte werden zuverlässig geschwärzt!
    for town in ("Frechen", "Bad Honnef", "Hürth-Efferen", "Pulheim", "Siegburg"):
        assert town not in res_on.redacted, f"Ortschaft wurde nicht geschwärzt: {town}"

    # Mit ausgeschalteter Checkbox bleiben Ortschaften stehen
    cfg_off = RedactionConfig(redact_locations=False)
    res_off = eng.analyze_cell(text, cfg_off)
    assert "Frechen" in res_off.redacted
    assert "Pulheim" in res_off.redacted


def test_context_rich_company_detection_and_domain_matching():
    eng = _engine()
    cfg = RedactionConfig(redact_companies=True, redact_locations=True)
    text = (
        "Firma: Müller & Söhne Umzüge\n"
        "Subunternehmer: Blitz Transporte West\n"
        "Im Auftrag der Nordwest Logistik GmbH & Co. KG melden wir uns.\n"
        "Er arbeitet bei Weber Immobilien und nutzt Outlook.\n"
        "Bitte Rückmeldung an das Team von Nexora (kontakt@nexora.de).\n"
        "Genehmigung liegt bei der Stadt Köln."
    )
    res = eng.analyze_cell(text, cfg)

    # Alle Firmennamen über Kontext, Rechtsform, Branche & Domain erkannt und vollständig geschwärzt!
    for company in (
        "Müller & Söhne Umzüge",
        "Blitz Transporte West",
        "Nordwest Logistik GmbH & Co. KG",
        "Weber Immobilien",
        "Nexora",
    ):
        assert company not in res.redacted, f"Firmenname nicht vollständig geschwärzt: {company}\nErgebnis:\n{res.redacted}"

    # Behörden & allgemeine Software bleiben unberührt
    assert "Stadt Köln" in res.redacted
    assert "Outlook" in res.redacted


def test_no_false_positives_on_weitergeleitete_and_testkennzeichen():
    eng = _engine()
    cfg = RedactionConfig(
        applicant_first_name="Max",
        applicant_last_name="Mustermann",
        redact_companies=True,
        redact_locations=True,
    )
    msg1 = (
        "Weitergeleitete Nachricht:\n"
        "Max Mustermann (max.mustermann@example.invalid)\n"
        "Bitte keine Änderung.\n"
        "---\n"
        "Thomas Muster: Genehmigung liegt vor."
    )
    res1 = eng.analyze_cell(msg1, cfg)
    assert "Weitergeleitete Nachricht:" in res1.redacted
    assert "Max Mustermann" in res1.redacted
    assert "Thomas Muster" not in res1.redacted
    assert "max.mustermann@example.invalid" not in res1.redacted

    msg2 = "M. Mustermann meldet: Fahrzeugkennzeichen B-XX 5379 (Testkennzeichen). Zufahrt freihalten."
    res2 = eng.analyze_cell(msg2, cfg)
    assert "M. Mustermann" in res2.redacted
    assert "(Testkennzeichen)" in res2.redacted
    assert "B-XX 5379" not in res2.redacted
