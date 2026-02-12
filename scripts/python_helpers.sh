#!/usr/bin/env bash
# SCRIPT: python_helpers.sh
# DESCRIPTION: Shared Python helper functions for repository shell scripts (source-only utility).
# USAGE: source ./scripts/python_helpers.sh
# PARAMETERS: Source this file from other scripts; do not execute directly.
# EXAMPLE: source ./scripts/python_helpers.sh
set -euo pipefail

python_version() {
  "$1" -c 'import sys; v = sys.version_info; print("{}.{}".format(v[0], v[1]))' 2>/dev/null || return 1
}

python_has_min_version() {
  local bin="$1"
  local min_major="$2"
  local min_minor="$3"
  local version
  version="$(python_version "$bin")" || return 1
  if ! [[ "$version" =~ ^[0-9]+\.[0-9]+$ ]]; then
    return 1
  fi
  local major="${version%%.*}"
  local minor="${version#*.}"
  if [[ "$major" -gt "$min_major" ]]; then
    return 0
  fi
  if [[ "$major" -eq "$min_major" && "$minor" -ge "$min_minor" ]]; then
    return 0
  fi
  return 1
}

python_can_run() {
  local bin="$1"
  if [[ -z "$bin" ]]; then
    return 1
  fi
  if [[ "$bin" == */* ]]; then
    if [[ -x "$bin" ]]; then
      return 0
    fi
    return 1
  fi
  command -v "$bin" >/dev/null 2>&1
}

pick_python3() {
  for candidate in python3 python; do
    if python_can_run "$candidate"; then
      if python_has_min_version "$candidate" 3 8; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

resolve_python3() {
  local requested="${1:-}"

  if [[ -n "$requested" ]]; then
    local picked
    if python_can_run "$requested" && python_has_min_version "$requested" 3 8; then
      echo "$requested"
      return 0
    fi
    if picked="$(pick_python3)"; then
      echo "$picked"
      return 0
    fi
    return 1
  fi

  pick_python3
}

ensure_venv() {
  local python_bin="$1"
  local venv_dir="$2"

  if [[ -z "$python_bin" || -z "$venv_dir" ]]; then
    return 1
  fi

  safe_to_remove_venv() {
    local target="$1"
    if [[ -z "$target" ]]; then
      return 1
    fi
    local resolved
    resolved="$(cd "$target" 2>/dev/null && pwd -P)" || return 1
    local home_dir=""
    if [[ -n "${HOME:-}" ]]; then
      home_dir="$(cd "$HOME" 2>/dev/null && pwd -P)" || home_dir=""
    fi
    if [[ "$resolved" == "/" || "$resolved" == "." || "$resolved" == "/home" || "$resolved" == "/Users" ]]; then
      return 1
    fi
    if [[ -n "$home_dir" && "$resolved" == "$home_dir" ]]; then
      return 1
    fi
    if [[ ! -f "$resolved/pyvenv.cfg" ]]; then
      return 1
    fi
    return 0
  }

  if [[ -x "$venv_dir/bin/python" ]]; then
    if ! python_has_min_version "$venv_dir/bin/python" 3 8; then
      if ! safe_to_remove_venv "$venv_dir"; then
        echo "Refusing to remove unsafe virtualenv path: $venv_dir" >&2
        echo "Set VENV_DIR to a dedicated virtualenv directory and retry." >&2
        return 1
      fi
      echo "Existing virtualenv at $venv_dir uses Python < 3.8; recreating." >&2
      rm -rf "$venv_dir"
    fi
  fi

  if [[ ! -x "$venv_dir/bin/python" ]]; then
    if ! "$python_bin" -m venv "$venv_dir"; then
      echo "Failed to create virtualenv at $venv_dir. Ensure python3-venv is installed." >&2
      return 1
    fi
  fi

  echo "$venv_dir/bin/python"
}
