#!/usr/bin/env bash
# SCRIPT: bump_version.sh
# DESCRIPTION: Bump VERSION, which is the single source of truth for package metadata.
# USAGE: ./scripts/bump_version.sh <major|minor|patch|X.Y.Z>
# PARAMETERS: One required argument: bump mode or explicit version.
# EXAMPLE: ./scripts/bump_version.sh patch
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"
VERSION_FILE="$ROOT_DIR/VERSION"

if [[ ! -f "$VERSION_FILE" ]]; then
  echo "VERSION file not found." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <major|minor|patch|X.Y.Z>" >&2
  exit 1
fi

input="$1"

PYTHON_BIN="${PYTHON_BIN:-}"
if ! PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN")"; then
  echo "Failed to locate a usable Python 3.8+ interpreter." >&2
  echo "Ensure Python 3.8+ is installed and on your PATH, or set PYTHON_BIN to a valid Python 3.8+ executable." >&2
  exit 1
fi

"$PYTHON_BIN" - "$VERSION_FILE" "$input" <<'PY'
import re
import sys

version_path = sys.argv[1]
value = sys.argv[2].strip()

with open(version_path, "r", encoding="utf-8") as handle:
    current = handle.read().strip()

def bump(cur, kind):
    major, minor, patch = (int(x) for x in cur.split("."))
    if kind == "major":
        return f"{major+1}.0.0"
    if kind == "minor":
        return f"{major}.{minor+1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch+1}"
    return None

new_version = bump(current, value)
if new_version is None:
    if not re.match(r"^\d+\.\d+\.\d+$", value):
        raise SystemExit(f"Invalid version: {value}")
    new_version = value

with open(version_path, "w", encoding="utf-8") as handle:
    handle.write(new_version + "\n")

print(new_version)
PY
