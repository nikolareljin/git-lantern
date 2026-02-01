#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Python 3.8+ is required but could not be resolved." >&2
  echo "Ensure Python 3.8+ is on your PATH or set PYTHON_BIN to a valid Python 3.8+ executable." >&2
  exit 1
fi

if ! PYTHON_BIN="$(ensure_venv "$PYTHON_BIN" "$VENV_DIR")"; then
  exit 1
fi

"$PYTHON_BIN" -m pip install ruff
ruff check "$ROOT_DIR"
