#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"
PYTHON_BIN="${PYTHON_BIN:-}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Python 3.8+ is required; install python3 or set PYTHON_BIN to a valid Python 3.8+ interpreter on PATH." >&2
  exit 1
fi

if ! PYTHON_BIN="$(ensure_venv "$PYTHON_BIN" "$VENV_DIR")"; then
  exit 1
fi

if [[ -d "$ROOT_DIR/tests" ]]; then
  "$PYTHON_BIN" -m pip install pytest
  "$PYTHON_BIN" -m pytest "$ROOT_DIR/tests"
  exit 0
fi

"$PYTHON_BIN" -m compileall "$ROOT_DIR/src"
echo "No tests found; compiled sources."
