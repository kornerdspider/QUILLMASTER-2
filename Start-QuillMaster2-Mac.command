#!/bin/bash
# Double-click to start Quill Master 2 on a Mac.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo ""
  echo "  First run: setting up Quill Master 2. This takes a minute or two..."
  echo ""
  if ! command -v python3 >/dev/null 2>&1; then
    echo "  Python 3 was not found. Install it from https://www.python.org/downloads/"
    echo "  then double-click this file again."
    read -r -p "  Press Return to close."
    exit 1
  fi
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install --upgrade pip
  if ! .venv/bin/python -m pip install -r requirements.txt; then
    echo "  Setup could not download what it needs. Check your internet, delete the"
    echo "  .venv folder in this folder, and try again."
    read -r -p "  Press Return to close."
    exit 1
  fi
  .venv/bin/python -m pip install -r requirements-desktop.txt || echo "  (Optional app-window support skipped. Quill Master will open in your browser.)"
fi

.venv/bin/python run.py "$@"
