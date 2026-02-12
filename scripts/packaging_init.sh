#!/usr/bin/env bash
# SCRIPT: packaging_init.sh
# DESCRIPTION: Initialize packaging templates through script-helpers.
# USAGE: ./scripts/packaging_init.sh
# PARAMETERS: No required parameters.
# EXAMPLE: ./scripts/packaging_init.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$ROOT_DIR/scripts/script-helpers/scripts/packaging_init.sh" ]]; then
  echo "script-helpers not initialized. Run ./scripts/update_submodules.sh" >&2
  exit 1
fi

bash "$ROOT_DIR/scripts/script-helpers/scripts/packaging_init.sh" --repo "$ROOT_DIR"
