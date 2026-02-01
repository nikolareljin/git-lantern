#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/python_helpers.sh"
MAN_DIR="$ROOT_DIR/man"

mkdir -p "$MAN_DIR"

if ! command -v help2man >/dev/null 2>&1; then
  echo "help2man not available; keeping existing man/lantern.1"
  exit 0
fi

PYTHON_BIN="${PYTHON_BIN:-}"
PYTHON_BIN="$(resolve_python3 "$PYTHON_BIN" || true)"

if [[ -n "$PYTHON_BIN" ]]; then
  wrapper="$(mktemp)"
  cat >"$wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" == "--version" ]]; then
  cat "$ROOT_DIR/VERSION"
  exit 0
fi
if command -v lantern >/dev/null 2>&1; then
  exec lantern "\$@"
fi
PYTHONPATH="$ROOT_DIR/src\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON_BIN" -m lantern "\$@"
EOF
  chmod +x "$wrapper"
  trap 'rm -f "$wrapper"' EXIT
  help2man --no-discard-stderr -N -n "repository visibility toolkit" \
    --version-string "$(cat "$ROOT_DIR/VERSION")" \
    -o "$MAN_DIR/lantern.1" "$wrapper"
  echo "Man page generated via help2man."
  exit 0
fi

if command -v lantern >/dev/null 2>&1; then
  help2man --no-discard-stderr -N -n "repository visibility toolkit" \
    --version-string "$(cat "$ROOT_DIR/VERSION")" \
    -o "$MAN_DIR/lantern.1" lantern
  echo "Man page generated via help2man."
  exit 0
fi

echo "help2man or lantern command not available; keeping existing man/lantern.1"
