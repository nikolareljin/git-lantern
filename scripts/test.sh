#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

pick_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version="$(python_version "$candidate")"
      case "$version" in
        3.*) echo "$candidate"; return 0 ;;
      esac
    fi
  done
  return 1
}

if [[ -z "$PYTHON_BIN" ]]; then
  if ! PYTHON_BIN="$(pick_python)"; then
    echo "python3 is required but was not found on PATH." >&2
    exit 1
  fi
else
  PYTHON_VERSION="$(python_version "$PYTHON_BIN")"
  case "$PYTHON_VERSION" in
    3.*) ;;
    *)
      if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
      else
        echo "Python 3 is required; found ${PYTHON_VERSION} via ${PYTHON_BIN}." >&2
        exit 1
      fi
      ;;
  esac
fi

if [[ -d "$ROOT_DIR/tests" ]]; then
  "$PYTHON_BIN" -m pip install pytest
  "$PYTHON_BIN" -m pytest "$ROOT_DIR/tests"
  exit 0
fi

"$PYTHON_BIN" -m compileall "$ROOT_DIR/src"
echo "No tests found; compiled sources."
