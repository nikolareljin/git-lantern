#!/usr/bin/env bash
# SCRIPT: update_submodules.sh
# DESCRIPTION: Sync and initialize git submodules recursively.
# USAGE: ./scripts/update_submodules.sh
# PARAMETERS: No required parameters.
# EXAMPLE: ./scripts/update_submodules.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

git -C "$ROOT_DIR" submodule sync --recursive
git -C "$ROOT_DIR" submodule update --init --recursive
echo "Submodules updated."
