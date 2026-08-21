#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-/etc/kubernetes/super-admin.conf}"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
NAMESPACE="literature-downloader"
API_IMAGE="${REGISTRY}/literature-downloader-api:${IMAGE_TAG}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-1200s}"
K="--kubeconfig=${KUBECONFIG_PATH}"

cd "$ROOT_DIR"

echo "Building ${API_IMAGE}"
docker build \
  --build-arg REGISTRY="$REGISTRY" \
  -f literature_downloader/Dockerfile.api \
  -t "$API_IMAGE" .
docker push "$API_IMAGE"

echo "Applying Literature Downloader API resources"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml $K | kubectl apply $K -f -

# The API launches one SciHub Job per collection round. Keep its shared
# CephFS PVC in the same namespace so the Job and API can mount it directly.
kubectl apply $K -f scihub_cli/namespace.yaml
kubectl apply $K -f scihub_cli/pvc.yaml

kubectl create configmap api-agent -n "$NAMESPACE" \
  --from-file=agent.py="app/api/literature_downloader.py" \
  --dry-run=client -o yaml $K | kubectl apply $K -f -

# Match the shared Agent LLM configuration. The API key remains optional so
# the deterministic search fallback continues to work without Vault.
sed "s/__NAMESPACE__/${NAMESPACE}/g" \
  literature_downloader/k8s/configmap.yaml \
  | kubectl apply $K -f -

sed \
  -e "s|__API_IMAGE__|${API_IMAGE}|g" \
  literature_downloader/k8s/deployment.yaml \
  | kubectl apply $K -f -

echo "Applying the shared Gradio UI image and template"
bash scripts/deploy-ui.sh literature_downloader

kubectl rollout restart deployment/api -n "$NAMESPACE" $K
kubectl rollout status deployment/api -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT" $K
kubectl rollout status deployment/ui -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT" $K
kubectl get pods,svc,pvc -n "$NAMESPACE" -o wide $K

echo "Literature Downloader: https://literature-downloader.panghuer.top"
