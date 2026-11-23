#!/bin/bash
# Start Quill Master 2 on Linux. Run:  ./start-quillmaster2-linux.sh
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Quill Master 2..."
  if ! python3 -m venv .venv; then
    echo "Python's venv module is missing. On Linux Mint / Ubuntu run:"
    echo "  sudo apt install python3-venv"
    exit 1
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi

.venv/bin/python run.py "$@"
