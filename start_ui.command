#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Funktioniert auch nach Umbenennen oder Verschieben des Ordners (kein Hardcoded-Pfad über activate)
if [ ! -x "$DIR/.venv/bin/python" ] && [ ! -x "$DIR/.venv/bin/python3" ]; then
  echo "Lokale Umgebung (.venv) noch nicht eingerichtet. Starte zuerst setup_mac.command..."
  ./setup_mac.command
fi

PY_BIN="$DIR/.venv/bin/python"
if [ ! -x "$PY_BIN" ]; then
  PY_BIN="$DIR/.venv/bin/python3"
fi

exec "$PY_BIN" -m dsgvo_redactor.web_app "$@"
