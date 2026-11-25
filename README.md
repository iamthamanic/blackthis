# BLACKTHIS – 100 % lokaler DSGVO-Schwärzer für CSV, Excel & PDF

Ein hochpräzises Offline-Tool zum automatischen und manuellen Schwärzen sensibler Daten Dritter in **CSV-**, **Excel- (`.xlsx`)** und **PDF-Dateien** für **DSGVO Art. 15 Auskunftsersuchen** — **100 % lokal auf deinem Rechner, ohne Cloud und ohne Chat-LLM**.

---

## Schnellstart per npm (macOS, Linux & Windows)

### Direkt starten (ohne manuelles Setup)
```bash
npx blackthis
```
Oder einmalig global installieren, damit überall im Terminal **`blackthis`** reicht:
```bash
npm install -g blackthis
blackthis
```

- **Beim ersten Start** richtet `blackthis` automatisch eine dauerhafte lokale Python-Umgebung in `~/.blackthis/venv` ein und lädt einmalig das deutsche Sprachmodell (`de_core_news_lg`, ~550 MB).
- **Automatischer Update-Check:** Beim Start prüft `blackthis` kurz (max. 2 s), ob auf npm eine neuere Version liegt, und fragt interaktiv, ob direkt aktualisiert & neu gestartet werden soll (funktioniert bei `npx blackthis` und globaler Installation; offline wird der Check still übersprungen).
- **Bei jedem weiteren Start** öffnet sich sofort die lokale Browser-Oberfläche auf `127.0.0.1` (mit automatischer Port-Suche, falls Port `8765` belegt ist) — **Datenverarbeitung bleibt 100 % offline**.

### Weitere Befehle
```bash
blackthis --port 8799         # Bevorzugten Port setzen
blackthis --no-open           # Server starten, ohne Browser zu öffnen
blackthis --setup             # Lokale Umgebung & Sprachmodell manuell einrichten/reparieren
blackthis --update            # Direkt auf die neueste npm-Version aktualisieren
blackthis --no-update-check   # Update-Prüfung beim Start überspringen
blackthis --cli --input datei.csv --vorname Max --nachname Mustermann
blackthis --cli --input dokument.pdf --vorname Max --nachname Mustermann
```

---

## Highlights & Funktionen

### Unterstützte Formate
- **CSV (`.csv` / `.txt`):** Automatische Erkennung von Encoding (UTF-8, UTF-8-BOM, CP1252/Latin-1), Trennzeichen (`;`, `,`, Tab, `|`) und mehrzeiligen Zellen.
- **Excel (`.xlsx`):** Direktes Einlesen & Exportieren als formatierte Arbeitsmappe.
- **PDF (`.pdf`) – echte In-Place-Schwärzung:** Original-Layout, Tabellen und Briefköpfe bleiben 1:1 erhalten; geschwärzter Text, überlappende `mailto:`/`tel:`/URL-Links und PDF-Metadaten werden **unwiderruflich** aus dem PDF entfernt. Inkl. visueller **PDF-Vorschau (Original vs. Geschwärzt)** im Browser und automatischer Warnung bei reinen Bild-Scans ohne Text-Ebene.

### Erkennung & Datenschutz-Logik
- **Klarnamen (Dritte):** Anreden, Kontextregeln, E-Mail-Signaturen, JSON-/HTML-Payloads, Intra-Zellen-Namenspropagation sowie lokale deutsche Sprach-KI (`spaCy de_core_news_lg`) kombiniert mit lokalem deutschem Häufigkeits-Wörterbuch (`wordfreq`) gegen Fehlalarme bei normalen Substantiven.
- **Antragsteller-Schutz:** Vor- und Nachname des Antragstellers (inkl. Anreden, Initialen und Umlaut-Varianten wie `Hübner` ↔ `Huebner`) bleiben sichtbar, ohne fremde Namensvettern zu leaken.
- **Strukturierte PII:** Adressen, Straßen, Hausnummern, PLZ (DE/AT/CH), Ortschaften (Behörden wie `Stadt Köln` bleiben sichtbar), IBAN (ISO-13616-Prüfziffer) / BIC / Bankdaten, Job-Positionen, Geldbeträge, Telefonnummern, E-Mails, URLs, IP-Adressen, Geburtsdaten, Kfz-Kennzeichen, Steuer-/USt-IDs sowie optional Firmennamen.

### Interaktive Prüfung & Exporte
- **Gelbe Prüffälle-Navigator:** Nummerierte Badges (`#1`, `#2`, …), Sprung-Buttons (`← Zurück | Check | Vor →`) und automatisch einblendende Prüf-Leiste beim Scrollen.
- **1-Klick-Umschalten & Maus-Markierung:** Beliebigen Text im Original markieren → **🔒 Nur hier schwärzen** (lila), **🔒 Überall schwärzen** (blau) oder **✓ Überall freigeben** (grün).
- **Live-Schwärzungs-Text:** Jederzeit ohne neuen Scan umschaltbar zwischen `██████`, **`[GESCHWÄRZT]`**, **`[SECRET]`**, Kategorie-Platzhaltern (`[NAME]`, `[IBAN]`) oder eigenem Text.
- **Exporte:** Geschwärztes **PDF**, **CSV** oder **Excel (`.xlsx`)** sowie **Audit ohne Originale (CSV)**, **Interner Prüfbericht (HTML)** und **Internes Audit-Log (CSV)**.

---

## Direkt im Projektordner nutzen (macOS)

```bash
chmod +x setup_mac.command start_ui.command
./setup_mac.command
./start_ui.command
```

## Auf npmjs.com veröffentlichen

```bash
npm login
npm pack --dry-run   # Prüfen, welche Dateien genau im Paket landen
npm publish          # Veröffentlicht 'blackthis' (falls vergeben: in package.json z. B. '@deinname/blackthis' setzen)
```
