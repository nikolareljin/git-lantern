# bash completion for lantern using argcomplete
# shellcheck shell=bash

if ! command -v register-python-argcomplete >/dev/null 2>&1; then
  return 0
fi

eval "$(register-python-argcomplete lantern)"
