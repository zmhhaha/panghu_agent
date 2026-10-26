#!/usr/bin/env bash
# Build on the server and publish to its private registry by default.
set -Eeuo pipefail

script_path="${BASH_SOURCE[0]}"
script_parent=.
if [[ "$script_path" == */* ]]; then script_parent="${script_path%/*}"; fi
SCRIPT_DIR="$(CDPATH= cd -- "$script_parent" && pwd)"

registry_was_set="${REGISTRY+x}"
registry_override="${REGISTRY-}"
if [[ -f "${SCRIPT_DIR}/../../cluster_config.sh" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/../../cluster_config.sh"
fi
if [[ "$registry_was_set" == x ]]; then export REGISTRY="$registry_override"; fi
command -v python3 >/dev/null 2>&1 || { printf 'Python 3 is required.\n' >&2; exit 1; }
exec python3 "${SCRIPT_DIR}/build_image.py" "$@"
