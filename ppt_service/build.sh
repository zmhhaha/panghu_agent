#!/usr/bin/env bash
# Build the web/API site on this server, verify it, then publish to the private registry.
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: bash build.sh [--no-push]

Build and verify the PPT site, then push it (default).
--no-push builds and verifies locally without publishing or leaving a deployable digest.

Environment overrides:
  REGISTRY               Default: arm-cluster-master:5000
  IMAGE_TAG              Default: a fresh UTC timestamp
  IMAGE                  Complete registry/repository:tag; overrides REGISTRY/IMAGE_TAG
  PLATFORM               Default: linux/arm64
  NODE_BUILD_IMAGE       Default: node:22.20.0-bookworm-slim
  PYTHON_RUNTIME_IMAGE   Default: python:3.13.5-slim-bookworm
  PIP_INDEX_URL          Default: https://pypi.tuna.tsinghua.edu.cn/simple
  NPM_REGISTRY           Default: https://registry.npmmirror.com

Requires Bash, Python 3 and Docker on the build server. No host Node/pnpm or API image is needed.
Successful publishing records only the matching repository@sha256 digest in rendered/image.txt.
The separate Hermes worker build remains available as build_worker.sh.
EOF
}

push=true
while (($#)); do
  case "$1" in
    --no-push) push=false ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

script_path="${BASH_SOURCE[0]}"
script_parent=.
if [[ "$script_path" == */* ]]; then script_parent="${script_path%/*}"; fi
SCRIPT_DIR="$(CDPATH= cd -- "$script_parent" && pwd)"

# cluster_config.sh defines the local registry; an explicit caller override wins.
registry_was_set="${REGISTRY+x}"
registry_override="${REGISTRY-}"
if [[ -f "${SCRIPT_DIR}/../../cluster_config.sh" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/../../cluster_config.sh"
fi
if [[ "$registry_was_set" == x ]]; then REGISTRY="$registry_override"; fi
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
command -v python3 >/dev/null 2>&1 || { printf 'Python 3 is required.\n' >&2; exit 1; }
IMAGE_TAG="${IMAGE_TAG:-$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))')}"
IMAGE="${IMAGE:-${REGISTRY}/ppt-service-site:${IMAGE_TAG}}"

arguments=(--image "$IMAGE" --platform "${PLATFORM:-linux/arm64}")
if [[ "$push" == false ]]; then arguments+=(--no-push); fi
python3 "${SCRIPT_DIR}/deploy/k8s/build_server.py" "${arguments[@]}"
