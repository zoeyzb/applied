#!/bin/bash
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
export APPLYPILOT_DIR="${APPLYPILOT_DIR:-$HOME/.applypilot}"
python -m uvicorn server:app --host 127.0.0.1 --port 8787
