#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Failed to find a usable Python 3.8+ interpreter." >&2
  echo "Ensure Python 3.8+ is installed and on PATH, or set PYTHON_BIN to a valid Python 3.8+ executable." >&2
  exit 1
fi

if ! PYTHON_BIN="$(ensure_venv "$PYTHON_BIN" "$VENV_DIR")"; then
  exit 1
fi

"$PYTHON_BIN" -m pip install -e "$ROOT_DIR"
echo "Build complete."
