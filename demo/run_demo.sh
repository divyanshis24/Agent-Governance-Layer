#!/usr/bin/env bash
# Run the Aegis demo with Python 3.11+ (macOS `python` is often 2.7).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x backend/.venv/bin/python ]; then
  exec backend/.venv/bin/python demo/demo_scenario.py "$@"
fi
if command -v python3.11 >/dev/null 2>&1; then
  exec python3.11 demo/demo_scenario.py "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 demo/demo_scenario.py "$@"
fi
echo "Python 3.11+ required. Install with: brew install python@3.11" >&2
exit 1
