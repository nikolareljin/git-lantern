#!/usr/bin/env bash
# SCRIPT: release.sh
# DESCRIPTION: Create release artifacts and optionally create/push a version tag.
# USAGE: ./scripts/release.sh [--no-tag] [--tag-prefix PREFIX]
# PARAMETERS: Optional flags: --no-tag, --tag-prefix.
# EXAMPLE: ./scripts/release.sh --tag-prefix v
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"
DIST_DIR="$ROOT_DIR/dist"
no_tag=false
tag_prefix=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tag) no_tag=true; shift;;
    --tag-prefix) tag_prefix="$2"; shift 2;;
    -h|--help)
      echo "Usage: $0 [--no-tag] [--tag-prefix v]" >&2
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2;;
  esac
done

if [[ ! -f "$VERSION_FILE" ]]; then
  echo "VERSION file not found." >&2
  exit 1
fi

version="$(cat "$VERSION_FILE")"
if [[ -z "$version" ]]; then
  echo "VERSION is empty." >&2
  exit 1
fi

if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
  if [[ "${RELEASE_ALLOW_DIRTY:-}" != "1" ]]; then
    echo "Working tree is dirty; commit or stash before release." >&2
    echo "Set RELEASE_ALLOW_DIRTY=1 to override." >&2
    exit 1
  fi
fi

mkdir -p "$DIST_DIR"

if [[ ! -f "$ROOT_DIR/scripts/script-helpers/scripts/build_brew_tarball.sh" ]]; then
  echo "script-helpers not initialized. Run ./scripts/update_submodules.sh" >&2
  exit 1
fi

exclude_paths=".git,.github,dist,venv,__pycache__,data"
args=(--name "lantern" --repo "$ROOT_DIR" --dist-dir "$DIST_DIR")
IFS=',' read -r -a excludes <<< "$exclude_paths"
for ex in "${excludes[@]}"; do
  ex="$(echo "$ex" | xargs)"
  [[ -z "$ex" ]] && continue
  args+=(--exclude "$ex")
done

bash "$ROOT_DIR/scripts/script-helpers/scripts/build_brew_tarball.sh" "${args[@]}"

if ! $no_tag; then
  git -C "$ROOT_DIR" tag "${tag_prefix}${version}"
  echo "Created dist tarball and tag ${tag_prefix}${version}."
else
  echo "Created dist tarball."
fi
