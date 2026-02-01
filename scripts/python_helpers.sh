#!/usr/bin/env bash
set -euo pipefail

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

pick_python3() {
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

resolve_python3() {
  local requested="${1:-}"

  if [[ -n "$requested" ]]; then
    version="$(python_version "$requested")"
    case "$version" in
      3.*) echo "$requested"; return 0 ;;
      *)
        if command -v python3 >/dev/null 2>&1; then
          echo "python3"
          return 0
        fi
        return 1
        ;;
    esac
  fi

  pick_python3
}
