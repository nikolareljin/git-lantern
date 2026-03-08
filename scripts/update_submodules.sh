#!/usr/bin/env bash
# SCRIPT: update_submodules.sh
# DESCRIPTION: Sync and initialize git submodules recursively.
# USAGE: ./scripts/update_submodules.sh
# PARAMETERS: No required parameters.
# EXAMPLE: ./scripts/update_submodules.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$ROOT_DIR/.gitmodules" ]]; then
    echo "No .gitmodules found; nothing to update."
    exit 0
fi

configured_paths=()
while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    configured_paths+=("$path")
done < <(
    git -C "$ROOT_DIR" config -f .gitmodules --get-regexp '^submodule\..*\.path$' \
    | awk '{print $2}' || true
)

if [[ "${#configured_paths[@]}" -eq 0 ]]; then
    echo "No configured submodules found in .gitmodules."
    exit 0
fi

gitlink_paths=()
while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    gitlink_paths+=("$path")
done < <(
    git -C "$ROOT_DIR" ls-files -s \
    | awk '$1=="160000"{print $4}'
)

for gitlink_path in "${gitlink_paths[@]}"; do
    found=0
    for configured_path in "${configured_paths[@]}"; do
        if [[ "$gitlink_path" == "$configured_path" ]]; then
            found=1
            break
        fi
    done
    if [[ "$found" -eq 0 ]]; then
        echo "warning: skipping stale gitlink not present in .gitmodules: $gitlink_path" >&2
    fi
done

for path in "${configured_paths[@]}"; do
    found=0
    for gitlink_path in "${gitlink_paths[@]}"; do
        if [[ "$path" == "$gitlink_path" ]]; then
            found=1
            break
        fi
    done
    if [[ "$found" -eq 0 ]]; then
        echo "warning: skipping stale .gitmodules entry not present in index: $path" >&2
        continue
    fi
    git -C "$ROOT_DIR" submodule sync --recursive -- "$path"
    git -C "$ROOT_DIR" submodule update --init --recursive -- "$path"
done

echo "Submodules updated."
