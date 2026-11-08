#!/bin/zsh
# Klik dua kali di Finder untuk membuka tampilan desktop.
cd "$(dirname "$0")"
source ./find_python.sh
PY=$(find_python) || { echo "Python dengan Tk tidak ditemukan. Install Python dari python.org."; exit 1; }
"$PY" gui.py
