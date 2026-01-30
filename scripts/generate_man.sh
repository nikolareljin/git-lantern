#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAN_DIR="$ROOT_DIR/man"

mkdir -p "$MAN_DIR"

if command -v help2man >/dev/null 2>&1 && command -v lantern >/dev/null 2>&1; then
  help2man -N -n "repository visibility toolkit" -o "$MAN_DIR/lantern.1" lantern
  echo "Man page generated via help2man."
  exit 0
fi

echo "help2man or lantern command not available; keeping existing man/lantern.1"
