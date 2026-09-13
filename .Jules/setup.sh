#!/usr/bin/env bash
# Hint for Jules VM setup (also paste into Jules repo Environment if asked).
set -euo pipefail
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm ci || npm install
