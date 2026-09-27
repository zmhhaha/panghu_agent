#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
KUBECONFIG_ARG="${KUBECONFIG_ARG:---kubeconfig=/etc/kubernetes/super-admin.conf}"

cd "${ROOT_DIR}"
docker build -f Dockerfile.api -t "${REGISTRY}/agent-api:latest" .
docker push "${REGISTRY}/agent-api:latest"
docker build -f baijiazhengming/Dockerfile.ui -t "${REGISTRY}/baijiazhengming-ui:latest" .
docker push "${REGISTRY}/baijiazhengming-ui:latest"

kubectl apply ${KUBECONFIG_ARG} -f baijiazhengming/k8s.yaml
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-api -n panghu-agent --timeout=180s
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-ui -n panghu-agent --timeout=180s

echo "百家争鸣旁路部署完成；现有 OAuth/Cloudflare 路由尚未切换。"
