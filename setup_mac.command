#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "=============================================="
echo "  DSGVO CSV Schwärzer – Einrichtung (macOS)"
echo "=============================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "Fehler: python3 wurde nicht gefunden."
  echo "Bitte installiere Python 3.11 oder 3.12 (z. B. über https://www.python.org/downloads/macos/ oder 'brew install python@3.12')."
  exit 1
fi

echo "1/3 Erstelle lokale Python-Umgebung (.venv)..."
python3 -m venv .venv
source .venv/bin/activate

echo "2/3 Installiere Pakete..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "3/3 Lade lokales deutsches Sprachmodell (de_core_news_lg, ~550 MB, einmalig)..."
python -m spacy download de_core_news_lg

echo ""
echo "Fertig! Ab jetzt läuft alles 100 % offline auf deinem MacBook."
echo "Starte die Oberfläche per Doppelklick auf 'start_ui.command' oder im Terminal mit:"
echo "  ./start_ui.command"
echo ""
read -n 1 -s -r -p "Beliebige Taste zum Schließen drücken..."
