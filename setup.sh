#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "== ApplyPilot Web setup =="
command -v python3 >/dev/null || { echo "Python 3 is required."; exit 1; }

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# Original ApplyPilot package + web wrapper
pip install -r requirements.txt

# ApplyPilot README's JobSpy workaround
pip install --no-deps python-jobspy
pip install pydantic tls-client requests markdownify regex

echo
echo "Core install complete."
echo "For full auto-apply, also make sure you have:"
echo "  - Node.js 18+"
echo "  - Google Chrome"
echo "  - Claude Code CLI (the 'claude' command)"
echo
echo "Start with: ./start.sh"
