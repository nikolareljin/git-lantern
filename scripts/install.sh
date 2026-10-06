#!/usr/bin/env bash
# SCRIPT: install.sh
# DESCRIPTION: Install git-lantern into a target prefix and create launcher symlink.
# USAGE: ./scripts/install.sh [--dev] [--prefix PATH] [--bin-link PATH] [--dry-run]
# PARAMETERS: Optional flags: --dev, --prefix, --bin-link, --dry-run.
# EXAMPLE: ./scripts/install.sh --prefix "$HOME/.local/opt/git-lantern" --bin-link "$HOME/.local/bin/lantern"
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"

if [[ "$(uname -s)" == "Darwin" ]]; then
  DEFAULT_INSTALL_ROOT="$HOME/.local/opt/git-lantern"
  DEFAULT_BIN_LINK="$HOME/.local/bin/lantern"
else
  DEFAULT_INSTALL_ROOT="/opt/git-lantern"
  DEFAULT_BIN_LINK="/usr/local/bin/lantern"
fi

INSTALL_ROOT="${INSTALL_ROOT:-$DEFAULT_INSTALL_ROOT}"
VENV_DIR="${VENV_DIR:-$INSTALL_ROOT/venv}"
BIN_LINK="${BIN_LINK:-$DEFAULT_BIN_LINK}"
DEV_EXTRAS=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV_EXTRAS=true; shift ;;
    --prefix) INSTALL_ROOT="$2"; VENV_DIR="$INSTALL_ROOT/venv"; shift 2 ;;
    --bin-link) BIN_LINK="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if $DRY_RUN; then
  printf 'install_root=%s\nvenv_dir=%s\nbin_link=%s\n' "$INSTALL_ROOT" "$VENV_DIR" "$BIN_LINK"
  exit 0
fi

dir_is_writable() {
  local dir="$1"
  while [[ ! -d "$dir" ]]; do
    local parent
    parent="$(dirname "$dir")"
    if [[ "$parent" == "$dir" ]]; then
      break
    fi
    dir="$parent"
  done
  [[ -w "$dir" ]]
}

need_sudo=false
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if ! dir_is_writable "$INSTALL_ROOT"; then
    need_sudo=true
  fi
  if ! dir_is_writable "$(dirname "$BIN_LINK")"; then
    need_sudo=true
  fi
fi

SUDO=""
if $need_sudo; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required to install to the selected locations." >&2
    echo "Either install sudo or choose writable --prefix/--bin-link paths." >&2
    exit 1
  fi
  SUDO="sudo"
fi

run_cmd() {
  if [[ -n "$SUDO" ]]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

PYTHON_BIN="${PYTHON_BIN:-}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Failed to find a usable Python 3.8+ interpreter." >&2
  echo "Ensure Python 3.8+ is installed and on PATH, or set PYTHON_BIN to a valid Python 3.8+ executable." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required for repository synchronization." >&2
  echo "Install Git, then retry (on macOS: Xcode Command Line Tools or brew install git)." >&2
  exit 1
fi

run_cmd mkdir -p "$INSTALL_ROOT"
run_cmd mkdir -p "$(dirname "$BIN_LINK")"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  if ! run_cmd "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    echo "Failed to create virtualenv at $VENV_DIR. Ensure python3-venv is installed." >&2
    exit 1
  fi
fi

run_cmd "$VENV_DIR/bin/python" -m pip install --upgrade pip

# Build the wheel as the invoking user from a clean copy of the source tree so
# stale or root-owned build artifacts in the checkout (build/, *.egg-info) can
# never break the build, and so a sudo install leaves no root-owned files behind.
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/git-lantern-install.XXXXXX")"
trap 'rm -rf "$STAGE_DIR"' EXIT
SRC_DIR="$STAGE_DIR/src"
WHEEL_DIR="$STAGE_DIR/wheels"
mkdir -p "$SRC_DIR" "$WHEEL_DIR"
tar -C "$ROOT_DIR" \
  --exclude='./.git' --exclude='./build' --exclude='./dist' \
  --exclude='./venv' --exclude='./.venv' --exclude='*.egg-info' \
  -cf - . | tar -C "$SRC_DIR" -xf -

if ! "$VENV_DIR/bin/python" -m pip wheel --no-deps --wheel-dir "$WHEEL_DIR" "$SRC_DIR"; then
  echo "Failed to build the git-lantern wheel." >&2
  exit 1
fi
WHEEL_FILE="$(find "$WHEEL_DIR" -maxdepth 1 -name 'git_lantern-*.whl' | head -n 1)"
if [[ -z "$WHEEL_FILE" ]]; then
  echo "Built wheel not found in $WHEEL_DIR." >&2
  exit 1
fi

if $DEV_EXTRAS; then
  run_cmd "$VENV_DIR/bin/pip" install --upgrade --force-reinstall "${WHEEL_FILE}[dev]"
else
  run_cmd "$VENV_DIR/bin/pip" install --upgrade --force-reinstall "$WHEEL_FILE"
fi

run_cmd ln -sf "$VENV_DIR/bin/lantern" "$BIN_LINK"
run_cmd "$BIN_LINK" --version

echo "Installed lantern to $BIN_LINK"
echo "User config stays per-user at ~/.git-lantern/config.json or ~/.config/git-lantern/config.json"
