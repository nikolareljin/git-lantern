#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"

PYTHON_BIN="${PYTHON_BIN:-}"

if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "python3 is required but was not found on PATH." >&2
  exit 1
fi

"$PYTHON_BIN" -m pip install -e "$ROOT_DIR"
echo "Build complete."
