#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
VAULT_MANIFEST="${ROOT_DIR}/../vault/inventory/content-llm-externalsecret.yaml"
if [[ "${1:-}" != "--skip-build" ]]; then
  REGISTRY="${REGISTRY}" IMAGE_TAG="${IMAGE_TAG}" bash "${SCRIPT_DIR}/build.sh"
fi
kubectl apply -f "${VAULT_MANIFEST}"
if kubectl -n vault exec vault-0 -- vault kv get -field=DEEPSEEK_API_KEY secret/content-agents/llm >/dev/null 2>&1; then
  kubectl -n content-agents wait --for=condition=Ready externalsecret/content-llm-secret --timeout=120s
else
  echo "[content-llm] WARNING: Vault secret/content-agents/llm not found; service will remain degraded until configured."
fi
sed "s|arm-cluster-master:5000/panghu-content-llm:latest|${REGISTRY}/panghu-content-llm:${IMAGE_TAG}|g" "${SCRIPT_DIR}/k8s.yaml" | kubectl apply -f -
# `latest` leaves the Deployment template unchanged, so apply alone would keep
# serving the old image. Restart explicitly after a successful build/apply.
kubectl -n content-agents rollout restart deployment/content-llm-service
kubectl -n content-agents rollout status deployment/content-llm-service --timeout=300s
