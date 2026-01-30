#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -d "$ROOT_DIR/tests" ]]; then
  python -m pip install pytest
  python -m pytest "$ROOT_DIR/tests"
  exit 0
fi

python -m compileall "$ROOT_DIR/src"
echo "No tests found; compiled sources."
