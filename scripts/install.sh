#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"

INSTALL_ROOT="${INSTALL_ROOT:-/opt/git-lantern}"
VENV_DIR="${VENV_DIR:-$INSTALL_ROOT/venv}"
BIN_LINK="${BIN_LINK:-/usr/local/bin/lantern}"
DEV_EXTRAS=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV_EXTRAS=true; shift ;;
    --prefix) INSTALL_ROOT="$2"; VENV_DIR="$INSTALL_ROOT/venv"; shift 2 ;;
    --bin-link) BIN_LINK="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required to install system-wide." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Failed to find a usable Python 3.8+ interpreter." >&2
  echo "Ensure Python 3.8+ is installed and on PATH, or set PYTHON_BIN to a valid Python 3.8+ executable." >&2
  exit 1
fi

sudo mkdir -p "$INSTALL_ROOT"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! sudo "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    echo "Failed to create virtualenv at $VENV_DIR. Ensure python3-venv is installed." >&2
    exit 1
  fi
fi

sudo "$VENV_DIR/bin/python" -m pip install --upgrade pip
if $DEV_EXTRAS; then
  sudo "$VENV_DIR/bin/pip" install --upgrade "${ROOT_DIR}[dev]"
else
  sudo "$VENV_DIR/bin/pip" install --upgrade "$ROOT_DIR"
fi

sudo ln -sf "$VENV_DIR/bin/lantern" "$BIN_LINK"

echo "Installed lantern to $BIN_LINK"
echo "User config stays per-user at ~/.git-lantern/config.json or ~/.config/git-lantern/config.json"
