#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"
PYTHON_BIN="${PYTHON_BIN:-}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

if [[ -d "$ROOT_DIR/tests" ]]; then
  "$PYTHON_BIN" -m pip install pytest
  "$PYTHON_BIN" -m pytest "$ROOT_DIR/tests"
  exit 0
fi

"$PYTHON_BIN" -m compileall "$ROOT_DIR/src"
echo "No tests found; compiled sources."
