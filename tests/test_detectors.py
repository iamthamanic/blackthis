"""Tiefentests für alle Detektoren inkl. realer Screenshot-Muster und False-Positive-Schutz."""

from dsgvo_redactor.detectors import run_rule_detectors
from dsgvo_redactor.models import Category, RedactionConfig


def _categories(text: str, config: RedactionConfig | None = None) -> set[Category]:
    cfg = config or RedactionConfig()
    return {m.category for m in run_rule_detectors(text, cfg)}


def _texts_by_cat(text: str, cat: Category, config: RedactionConfig | None = None) -> list[str]:
    cfg = config or RedactionConfig()
    return [m.text for m in run_rule_detectors(text, cfg) if m.category == cat]


def test_screenshot_row4_zu_haenden_and_no_false_positives():
    text = (
        "!!!Bitte bei der Stadt Köln bis spätestens Freitag einreichen, "
        "zu Händen Frau Sabine Becker. Ist abgesprochen, dass der 21.04.2026 möglich ist. "
        "Es kommt ein LKW 7,5t. Danke!"
    )
    matches = run_rule_detectors(text, RedactionConfig())
    names = [m.text for m in matches if m.category == Category.NAME]
    assert any("Sabine Becker" in n for n in names)
    # 7,5t darf NICHT als Geldbetrag erkannt werden; 21.04.2026 NICHT als Geburtsdatum; Stadt Köln NICHT geschwärzt
    assert all(m.category != Category.AMOUNT for m in matches)
    assert all(m.category != Category.BIRTHDATE for m in matches)
    assert all("Stadt Köln" not in m.text for m in matches)


def test_screenshot_row6_bank_and_amount():
    text = (
        "Kontoinhaber : Lukas Weber\n"
        "IBAN: DE89 3704 0044 0532 0130 00\n"
        "Betrag: 149,50"
    )
    matches = run_rule_detectors(text, RedactionConfig())
    assert any(m.category == Category.NAME and "Lukas Weber" in m.text for m in matches)
    assert any(m.category == Category.BANK and "DE89 3704 0044 0532 0130 00" in m.text for m in matches)
    assert any(m.category == Category.AMOUNT and "149,50" in m.text for m in matches)


def test_iban_with_typo_in_context_still_caught():
    text = "IBAN: DE89 3704 0044 0532 0130 99"
    bank_hits = _texts_by_cat(text, Category.BANK)
    assert any("DE89 3704 0044 0532 0130 99" in b for b in bank_hits)


def test_screenshot_row9_job_title_street_passive_agent_signoff():
    text = (
        "Nach Rücksprache mit der Stadt Köln, Sachbearbeiterin Claudia Hoffmann soll die Genehmigung "
        "für Curtiusstr. 6 beantragt werden. Dies wird dann durch Kowalski genehmigt.\n\n"
        "Ich bitte um Bestätigung der Änderung.\n\n"
        "Mit freundlichen Grüßen\n"
        "Stefan Klein"
    )
    matches = run_rule_detectors(text, RedactionConfig())
    assert any(m.category == Category.JOB_TITLE and m.text == "Sachbearbeiterin" for m in matches)
    assert any(m.category == Category.ADDRESS and "Curtiusstr. 6" in m.text for m in matches)
    names = [m.text for m in matches if m.category == Category.NAME]
    assert any("Claudia Hoffmann" in n for n in names)
    assert any("Kowalski" in n for n in names)
    assert any("Stefan Klein" in n for n in names)


def test_screenshot_row10_order_number_not_kfz_or_phone():
    text = (
        "Deshalb möchte ich den Auftrag mit der Bestellnummer HV123-396094 stornieren."
    )
    matches = run_rule_detectors(text, RedactionConfig())
    assert matches == []


def test_screenshot_row12_outlook_images_not_urls_and_signature_caught():
    text = (
        "BG\n\n"
        "Outlook-pspeh4mm.jpg\n"
        "Outlook-astesoil.png\n"
        "linkedin\n\n"
        "Julia Bergmann\n"
        "Projektleiterin\n"
        "Venloer Str. 214a, 50823 Köln\n"
        "Tel.: +49 221 5551234\n"
        "www.musterumzug-koeln.de"
    )
    matches = run_rule_detectors(text, RedactionConfig())
    urls = [m.text for m in matches if m.category == Category.WEBSITE]
    assert "www.musterumzug-koeln.de" in urls
    assert all("Outlook-" not in u for u in urls)
    assert any(m.category == Category.NAME and "Julia Bergmann" in m.text for m in matches)
    assert any(m.category == Category.JOB_TITLE and "Projektleiterin" in m.text for m in matches)
    assert any(m.category == Category.ADDRESS and "Venloer Str. 214a" in m.text for m in matches)
    assert any(m.category == Category.ADDRESS and "50823 Köln" in m.text for m in matches)
    assert any(m.category == Category.PHONE and "+49 221 5551234" in m.text for m in matches)


def test_companies_off_by_default_and_on_when_enabled():
    text = "Wir sind von der RheinLogistik Umzüge GmbH. Fahrzeuge sind alle 3,5 Tonnen."
    cfg_off = RedactionConfig(redact_companies=False)
    assert Category.COMPANY not in _categories(text, cfg_off)
    assert Category.AMOUNT not in _categories(text, cfg_off)

    cfg_on = RedactionConfig(redact_companies=True)
    comp = _texts_by_cat(text, Category.COMPANY, cfg_on)
    assert any("RheinLogistik Umzüge" in c for c in comp)


def test_extra_ids_birthdate_kfz_tax():
    text = "Geboren am 14.03.1984, Kennzeichen K-AB 4821, Steuer-ID 86095742719, USt-IdNr: DE123456789."
    matches = run_rule_detectors(text, RedactionConfig())
    assert any(m.category == Category.BIRTHDATE and m.text == "14.03.1984" for m in matches)
    assert any(m.category == Category.LICENSE_PLATE and m.text == "K-AB 4821" for m in matches)
    assert any(m.category == Category.TAX_ID and "86095742719" in m.text for m in matches)
    assert any(m.category == Category.TAX_ID and "DE123456789" in m.text for m in matches)


def test_phones_at_line_start_and_slash_with_spaces():
    text = (
        "Kontakt\n"
        "030 / 0000 8517\n"
        "Berlin\n"
        "0049 30 0000 7916\n"
        "Musterfirma\n"
        "+49 30 0000 5333"
    )
    phones = _texts_by_cat(text, Category.PHONE)
    assert "030 / 0000 8517" in phones
    assert "0049 30 0000 7916" in phones
    assert "+49 30 0000 5333" in phones


def test_sachbearbeitung_and_capitalized_prefix_before_fullname():
    text = (
        "Sachbearbeitung Erika Beispiel hat geantwortet.\n"
        "Rückfrage Lukas Weber liegt vor."
    )
    names = _texts_by_cat(text, Category.NAME)
    assert any("Erika Beispiel" in n for n in names)
    assert any("Lukas Weber" in n for n in names)


def test_enhanced_german_and_international_name_patterns():
    text = (
        "Name: Krzysztof Kowalczyk\n"
        "Kunde: O'Connor\n"
        "Anruf von Zielinski: herr schmidt und fr. becker bitten um Rückruf.\n"
        "Kowalski hat angerufen und SABINE BECKER schrieb eine Mail.\n"
        "Gezeichnet: K. Nowakowski\n"
        "Viele Grüße, Murat Şahin\n"
        "LG Svenja"
    )
    names = _texts_by_cat(text, Category.NAME)
    joined = " | ".join(names)
    for expected in (
        "Krzysztof Kowalczyk",
        "O'Connor",
        "Zielinski",
        "schmidt",
        "becker",
        "Kowalski",
        "SABINE BECKER",
        "K. Nowakowski",
        "Murat Şahin",
        "Svenja",
    ):
        assert expected in joined, f"Klarname nicht erkannt: {expected} (gefunden: {names})"


def test_new_formats_ip_birthdates_bic_at_ch_plz_and_space_kfz():
    text = (
        "Login von 192.168.10.42 (nicht Version 1.2.3.4). "
        "Geboren am 14/03/1984 bzw. geb. 14. März 1984. "
        "Überweisung über COBADEFFXXX nach A-1010 Wien und CH-8001 Zürich (PLZ: 8001). "
        "Wagen M AB 1234 gesichtet, Referenz DE123456789."
    )
    matches = run_rule_detectors(text, RedactionConfig())
    by_cat = {}
    for m in matches:
        by_cat.setdefault(m.category, []).append(m)

    assert any(m.text == "192.168.10.42" for m in by_cat.get(Category.IP_ADDRESS, []))
    assert all(m.text != "1.2.3.4" for m in by_cat.get(Category.IP_ADDRESS, []))

    bds = [m.text for m in by_cat.get(Category.BIRTHDATE, [])]
    assert "14/03/1984" in bds
    assert "14. März 1984" in bds

    assert any(m.text == "COBADEFFXXX" for m in by_cat.get(Category.BANK, []))
    addr_and_plz = [m.text for m in by_cat.get(Category.ADDRESS, []) + by_cat.get(Category.POSTAL_CODE, [])]
    assert any("A-1010 Wien" in x for x in addr_and_plz)
    assert any("CH-8001 Zürich" in x for x in addr_and_plz)
    assert "8001" in addr_and_plz

    kfz_hits = by_cat.get(Category.LICENSE_PLATE, [])
    assert any(m.text == "M AB 1234" and m.confidence.value == "REVIEW" for m in kfz_hits)

    # Freistehende USt-IdNr ohne Label -> REVIEW (DSGVO-sicher geschwärzt, aber gelb prüfbar)
    tax_hits = by_cat.get(Category.TAX_ID, [])
    assert any(m.text == "DE123456789" and m.confidence.value == "REVIEW" for m in tax_hits)


def test_street_without_space_after_dot_and_address_block_company():
    # 1. Straße ohne Leerzeichen nach Abkürzungspunkt + Firmenname direkt im Adressblock darüber
    block = "Der Packesel\nAlte Heroldstr.23\n50674 Köln"
    cfg = RedactionConfig(redact_companies=True)
    matches = run_rule_detectors(block, cfg)

    addrs = [m.text for m in matches if m.category == Category.ADDRESS]
    comps = [m for m in matches if m.category == Category.COMPANY]
    assert "Alte Heroldstr.23" in addrs
    assert any(m.text == "Der Packesel" and m.confidence.value == "REVIEW" for m in comps)

    # 2. Im normalen Fließtext OHNE Adressblock darf „Packesel“ NICHT als Firmenname fehlalarmieren
    prose = "Ich bin doch kein Packesel für die ganze Baustelle."
    prose_comps = [m for m in run_rule_detectors(prose, cfg) if m.category == Category.COMPANY]
    assert prose_comps == []

    # 3. Behörde oder Personenname über einer Straße darf NICHT fälschlich als Firmenname gelten
    auth_block = "Stadt Köln\nWilly-Brandt-Platz 2"
    assert [m for m in run_rule_detectors(auth_block, cfg) if m.category == Category.COMPANY] == []
    person_block = "Julia Bergmann\nVenloer Str.214a"
    p_matches = run_rule_detectors(person_block, cfg)
    assert any(m.text == "Venloer Str.214a" and m.category == Category.ADDRESS for m in p_matches)
    assert [m for m in p_matches if m.category == Category.COMPANY] == []


def test_json_payload_street_and_key_protection():
    from dsgvo_redactor.engine import RedactionEngine

    payload = (
        '{"strasse":"Kufsteiner 43","strasse2":null,"plz":"81679","bezirk":null,'
        '"ort":"München","unterort":null,"datum_von":"2026-04-23","uhr_von":"07:00",'
        '"from":null,"berater":null,"rechnung_group":null}'
    )
    payload_lower = '{"strasse":"ritterstrasse 22","plz":"50667","ort":"Köln"}'

    engine = RedactionEngine()
    cfg = RedactionConfig(use_ner=False)
    res1 = engine.analyze_cell(payload, cfg)
    res2 = engine.analyze_cell(payload_lower, cfg)

    # Straßenwerte (auch ohne „Straße“-Suffix und kleingeschrieben) werden zuverlässig geschwärzt
    assert "Kufsteiner 43" not in res1.redacted
    assert '"strasse":"██████"' in res1.redacted
    assert "81679" not in res1.redacted
    assert "München" not in res1.redacted
    assert "ritterstrasse 22" not in res2.redacted
    assert '"strasse":"██████"' in res2.redacted

    # JSON-Schlüsselnamen (wie "berater":null) und harmlose Felder bleiben unberührt
    assert '"berater":null' in res1.redacted
    assert '"from":null' in res1.redacted
    assert '"datum_von":"2026-04-23"' in res1.redacted
    assert '"uhr_von":"07:00"' in res1.redacted


def test_intra_cell_name_propagation_and_allcaps_signature_above_job_title():
    from dsgvo_redactor.engine import RedactionEngine

    engine = RedactionEngine()
    cfg = RedactionConfig(use_ner=False)

    # 1. Konkreter Fall: „Celeste“ + „CELESTE <Nachname>“ über „Senior Lead Producer“
    note = (
        "Doppelte Bestellung HI,\n\n"
        "Dieser Auftrag wird nicht benötigt. Die Hausnummer war falsch!\n\n"
        "Liebe Grüße\n"
        "Celeste\n\n"
        "CELESTE BERGMANN\n"
        "Senior Lead Producer"
    )
    res = engine.analyze_cell(note, cfg)
    assert "Celeste" not in res.redacted
    assert "CELESTE" not in res.redacted
    assert "BERGMANN" not in res.redacted
    assert "Senior Lead Producer" not in res.redacted
    assert "Die Hausnummer war falsch!" in res.redacted

    # 2. Völlig unbekannter seltener Name (nicht im Lexikon) wird per Intra-Zellen-Propagation auch in ALL-CAPS erkannt
    rare_note = "Liebe Grüße\nZoraya\n\nZORAYA meldet sich morgen."
    res_rare = engine.analyze_cell(rare_note, cfg)
    assert "Zoraya" not in res_rare.redacted
    assert "ZORAYA" not in res_rare.redacted


def test_ner_noise_filter_rejects_camelcase_and_snakecase_identifiers():
    from dsgvo_redactor.ner_engine import LocalGermanNER

    for tech_id in ("sendTemplateMail", "getCustomerData", "bcc_mail", "customer_ref", "datum_von"):
        assert LocalGermanNER._is_noise_entity(tech_id) is True
    assert LocalGermanNER._is_noise_entity("München") is False
    assert LocalGermanNER._is_noise_entity("Celeste") is False


def test_german_compound_nouns_not_flagged_as_names():
    text = (
        "Zufahrt für Krankenfahrzeuge und für Feuerwehrzufahrt bitte freihalten. "
        "Info für Anlieferung und bei Ausnahmegenehmigungen liegt vor. "
        "Rücksprache mit Herrn Jung und bei Kowalski."
    )
    names = _texts_by_cat(text, Category.NAME)
    joined = " | ".join(names)
    for forbidden in ("Krankenfahrzeuge", "Feuerwehrzufahrt", "Anlieferung", "Ausnahmegenehmigungen"):
        assert forbidden not in joined, f"Sach-Kompositum fälschlich als Name erkannt: {forbidden}"
    assert "Jung" in joined
    assert "Kowalski" in joined


def test_real_world_german_dictionary_words_and_hnr_not_false_positives():
    from dsgvo_redactor.engine import RedactionEngine

    text = (
        "Info für Baumschnittarbeiten, für Bauleitung, bei Möbellieferanten, "
        "fr. tourenplanung, fr. zusatzrechnung, durch Fluss, fr. ladetätigkeiten, "
        "für Baustellenlieferung, fr. differenzgeld, für Parktaschen, "
        "fr. pelletlieferanten, sehr geehrte Damen, mit Sattelzug, "
        "am Parkstreifen, für Zusatztag, konk, für Produktionsbüro, fenstermacher, Hnr 12. "
        "Rücksprache mit Herrn Fluss, Frau Neuhaus und bei Kowalski."
    )
    engine = RedactionEngine()
    res = engine.analyze_cell(text, RedactionConfig(use_ner=True))

    name_texts = {m.text.lower() for m in res.matches if m.category == Category.NAME}
    loc_texts = {m.text.lower() for m in res.matches if m.category == Category.LOCATION}

    for bad_name in (
        "zusatzrechnung", "baumschnittarbeiten", "bauleitung", "möbellieferanten",
        "tourenplanung", "ladetätigkeiten", "baustellenlieferung", "differenzgeld",
        "parktaschen", "pelletlieferanten", "geehrte", "sattelzug", "parkstreifen",
        "zusatztag", "konk", "produktionsbüro", "fenstermacher",
    ):
        assert bad_name not in name_texts, f"Fehlalarm als Klarname: {bad_name} (alle: {name_texts})"
    assert "hnr" not in loc_texts, f"Hnr fälschlich als Ortschaft erkannt: {loc_texts}"
    # „durch Fluss“ darf nicht matchen, aber „Herrn Fluss“ (mit expliziter Anrede!) sowie Neuhaus & Kowalski schon!
    assert "durch Fluss" in res.redacted
    assert "Neuhaus" not in res.redacted
    assert "Kowalski" not in res.redacted


def test_applicant_protected_inside_html_markup_and_umlaut_variants():
    from dsgvo_redactor.engine import RedactionEngine
    from dsgvo_redactor.ner_engine import _iter_line_segments

    # 1. NER-Segmentierung schneidet HTML-Tags sauber ab (inkl. angeschnittener Tags am Ende)
    html_line = '<tr><td><div><font><span>Max Mustermann</span></font></div></td></tr><tr><td style="padding'
    ent_start = html_line.index("Max Mustermann")
    segs = _iter_line_segments(html_line, ent_start, len(html_line))
    assert segs == [(ent_start, ent_start + len("Max Mustermann"), "Max Mustermann")]

    # 2. Antragsteller bleibt auch in HTML + vor deutschem Substantiv + vor Zeilenumbruch + Umlaut-Variante geschützt
    engine = RedactionEngine()
    cfg = RedactionConfig(applicant_first_name="Max", applicant_last_name="Hübner", use_ner=True)
    text = (
        '<td><span>Max Hübner</span></font></div></td></tr><tr><td style="padding"> '
        'Max Huebner Rückruf. Max\nRechnung liegt vor. '
        '<span>Sabine Becker</span></font> hat geantwortet. Max Meier ist ein Dritter.'
    )
    res = engine.analyze_cell(text, cfg)
    assert "Max Hübner</span>" in res.redacted
    assert "Max Huebner" in res.redacted
    assert "Max\nRechnung" in res.redacted
    # Dritte (inkl. anderer Nachname nach gleichem Vornamen!) werden weiterhin geschwärzt, HTML-Tags bleiben heil!
    assert "Sabine Becker" not in res.redacted
    assert "</span></font> hat geantwortet." in res.redacted
    assert "Max Meier" not in res.redacted
