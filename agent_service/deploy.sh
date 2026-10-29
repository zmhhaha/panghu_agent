#!/usr/bin/env bash
# Normal deployment: agent-service control plane and agent-workers capsules.
set -Eeuo pipefail
script_path="${BASH_SOURCE[0]}"
script_parent=.
if [[ "$script_path" == */* ]]; then script_parent="${script_path%/*}"; fi
SCRIPT_DIR="$(CDPATH= cd -- "$script_parent" && pwd)"
AGENT_SERVICE_PYTHON_BIN="${AGENT_SERVICE_PYTHON_BIN:-python3}"
command -v "$AGENT_SERVICE_PYTHON_BIN" >/dev/null 2>&1 || { printf 'Agent service Python is required.\n' >&2; exit 1; }
exec "$AGENT_SERVICE_PYTHON_BIN" "${SCRIPT_DIR}/deploy/deploy.py" "$@"
