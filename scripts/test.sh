#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "python (or python3) is required but was not found on PATH." >&2
    exit 1
  fi
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
  3.*) ;;
  *)
    echo "Python 3 is required; found ${PYTHON_VERSION} via ${PYTHON_BIN}." >&2
    exit 1
    ;;
esac

if [[ -d "$ROOT_DIR/tests" ]]; then
  "$PYTHON_BIN" -m pip install pytest
  "$PYTHON_BIN" -m pytest "$ROOT_DIR/tests"
  exit 0
fi

"$PYTHON_BIN" -m compileall "$ROOT_DIR/src"
echo "No tests found; compiled sources."
