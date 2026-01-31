#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"
PYPROJECT="$ROOT_DIR/pyproject.toml"

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

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "python (or python3) is required but was not found on PATH." >&2
    exit 1
  fi
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
  3.*) ;;
  *)
    echo "Python 3 is required; found ${PYTHON_VERSION} via ${PYTHON_BIN}." >&2
    exit 1
    ;;
esac

"$PYTHON_BIN" - "$VERSION_FILE" "$PYPROJECT" "$input" <<'PY'
import re
import sys

version_path = sys.argv[1]
pyproject_path = sys.argv[2]
value = sys.argv[3].strip()

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

with open(pyproject_path, "r", encoding="utf-8") as handle:
    content = handle.read()

content, count = re.subn(r'version = "\\d+\\.\\d+\\.\\d+"', f'version = "{new_version}"', content, count=1)
if count == 0:
    raise SystemExit("Failed to update version in pyproject.toml")

with open(pyproject_path, "w", encoding="utf-8") as handle:
    handle.write(content)

print(new_version)
PY
