#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT_DIR/scripts/update_submodules.sh"
bash "$ROOT_DIR/scripts/build.sh"
bash "$ROOT_DIR/scripts/lint.sh"
bash "$ROOT_DIR/scripts/test.sh"
echo "Local CI complete."
