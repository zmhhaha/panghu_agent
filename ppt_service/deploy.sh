#!/usr/bin/env bash
# Server build -> private registry -> isolated PPT website deployment.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export KUBECONFIG="${KUBECONFIG:-/etc/kubernetes/super-admin.conf}"
exec python3 "${SCRIPT_DIR}/deploy/k8s/deploy_site.py" "$@"
