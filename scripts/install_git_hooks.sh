#!/usr/bin/env bash
# SCRIPT: install_git_hooks.sh
# DESCRIPTION: Configure repository git hooks path to .githooks.
# USAGE: ./scripts/install_git_hooks.sh
# PARAMETERS: No required parameters.
# EXAMPLE: ./scripts/install_git_hooks.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

git -C "$ROOT_DIR" config core.hooksPath "$ROOT_DIR/.githooks"
echo "Git hooks installed (core.hooksPath -> $ROOT_DIR/.githooks)."
