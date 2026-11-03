#!/usr/bin/env bash
# Instance-only entry point for the separately registered fra6s004 study.
set -euo pipefail
[[ $# -eq 0 ]] || { printf 'Usage: setup-fra6s004-ollama.sh\n' >&2; exit 1; }
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$script_dir/setup-dell-ollama.sh" --site fra6s004
