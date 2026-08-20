#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Polymarket + Kalshi Dashboard"
echo "============================================"
echo

PYCMD=python3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 was not found. Install Python 3.10+ from https://python.org/downloads/"
    exit 1
fi

if ! $PYCMD -c "import flask, matplotlib, requests" &> /dev/null; then
    echo "Installing required packages, this only happens once..."
    $PYCMD -m pip install -r requirements.txt --break-system-packages 2>/dev/null \
        || $PYCMD -m pip install -r requirements.txt
fi

echo
echo "Starting the dashboard - your browser should open automatically."
echo "Leave this running while you use the dashboard; Ctrl+C to stop."
echo
$PYCMD app.py
