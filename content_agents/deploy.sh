#!/usr/bin/env bash
# Build and deploy the three content-agent CronJobs.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
KUBECONFIG="${KUBECONFIG:-/etc/kubernetes/super-admin.conf}"
export KUBECONFIG

SKIP_BUILD=false
if [[ "${1:-}" == "--skip-build" ]]; then
    SKIP_BUILD=true
fi

if [[ "${SKIP_BUILD}" == false ]]; then
    REGISTRY="${REGISTRY}" IMAGE_TAG="${IMAGE_TAG}" bash "${SCRIPT_DIR}/build.sh" --push
fi

kubectl apply -f "${SCRIPT_DIR}/k8s/namespace.yaml"
kubectl apply -f "${SCRIPT_DIR}/k8s/storage.yaml"
kubectl apply -f "${SCRIPT_DIR}/k8s/configmap.yaml"
sed "s|arm-cluster-master:5000|${REGISTRY}|g; s|:latest|:${IMAGE_TAG}|g" \
    "${SCRIPT_DIR}/k8s/cronjobs.yaml" | kubectl apply -f -

# The manifest is safe to apply before the Vault value exists. The CronJobs
# keep the token reference optional so JSON/RSS draft output can be tested first.
BOT_SECRET_MANIFEST="${ROOT_DIR}/../vault/inventory/content-agents-hublog-externalsecret.yaml"
if [[ -f "${BOT_SECRET_MANIFEST}" ]]; then
    kubectl apply -f "${BOT_SECRET_MANIFEST}"
else
    echo "[content-agents] WARNING: ${BOT_SECRET_MANIFEST} not found; Hublog channel will remain unavailable."
fi

echo "Content-agent CronJobs deployed. BOT_DRAFT_ONLY remains true until review is complete."
kubectl -n content-agents get cronjob,pvc -o wide
