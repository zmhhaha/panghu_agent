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
    if kubectl -n vault exec vault-0 -- vault kv get -field=HUBLOG_SERVICE_TOKENS secret/content-agents/auth >/dev/null 2>&1; then
        kubectl -n content-agents wait --for=condition=Ready externalsecret/content-agent-hublog --timeout=120s
    else
        echo "[content-agents] Vault raw-token entry not found; Hublog publications will be skipped until it is added."
    fi
else
    echo "[content-agents] WARNING: ${BOT_SECRET_MANIFEST} not found; Hublog channel will remain unavailable."
fi

draft_only="$(kubectl -n content-agents get configmap content-agents-config -o jsonpath='{.data.BOT_DRAFT_ONLY}')"
auto_approve="$(kubectl -n content-agents get configmap content-agents-config -o jsonpath='{.data.CONTENT_AUTO_APPROVE}')"
echo "Content-agent CronJobs deployed. BOT_DRAFT_ONLY=${draft_only:-unknown} CONTENT_AUTO_APPROVE=${auto_approve:-unknown}."
kubectl -n content-agents get cronjob,pvc -o wide
