#!/usr/bin/env bash
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
    [[ -x "$bin" ]]
    return $?
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
    if python_can_run "$requested" && python_has_min_version "$requested" 3 8; then
      echo "$requested"
      return 0
    fi
    if python_can_run python3 && python_has_min_version python3 3 8; then
      echo "python3"
      return 0
    fi
    return 1
  fi

  pick_python3
}
