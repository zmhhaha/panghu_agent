#!/bin/bash
# ============================================================
#  FastAPI 部署脚本 — ConfigMap 注入 agent.py + K8s apply
#
#  用法:
#    bash scripts/deploy-api.sh research_agent   # 研究助手
#    bash scripts/deploy-api.sh scientific_agent  # 科研综述
#
#  约定:
#    - 源码 app/api/<name>.py → ConfigMap api-agent (key: agent.py)
#    - 模板 k8s/api-deployment.yaml，__NAMESPACE__ placehold
# ============================================================
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"

NAME="${1:-research_agent}"
NAMESPACE="${NAME//_/-}"   # namespace
K="--kubeconfig=/etc/kubernetes/super-admin.conf"
SRC="../app/api/${NAME}.py"

echo "=== Deploying API: ${NAME} (namespace: ${NAMESPACE}) ==="

# build + push image
echo "=== Building image ==="
cd ..
docker build -f Dockerfile.api -t arm-cluster-master:5000/agent-api:latest .
docker push arm-cluster-master:5000/agent-api:latest
cd scripts

# ensure namespace
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml $K | kubectl apply $K -f -

# # 注释掉，避免重复部署
# # ConfigMap
# kubectl create configmap agent-config -n ${NAMESPACE} \
#     --from-literal=PROVIDER="custom" \
#     --from-literal=CUSTOM_API_BASE="${CUSTOM_API_BASE:-http://47.109.107.37/v1}" \
#     --from-literal=CUSTOM_MODEL="${CUSTOM_MODEL:-deepseek-v4-pro}" \
#     --dry-run=client -o yaml $K | kubectl apply $K -f -

# # 已由 Vault 管理，无需部署 Secret
# # Secret（已存在则跳过）
# kubectl get secret agent-secret -n ${NAMESPACE} $K >/dev/null 2>&1 || \
#     kubectl create secret generic agent-secret -n ${NAMESPACE} \
#         --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
#         --from-literal=CUSTOM_API_KEY="${CUSTOM_API_KEY:-}" \
#         $K --dry-run=client -o yaml | kubectl apply $K -f -

# ConfigMap (agent.py)
kubectl create configmap api-agent -n ${NAMESPACE} \
    --from-file=agent.py="${SRC}" \
    --dry-run=client -o yaml $K | kubectl apply $K -f -

# apply K8s
sed "s/__NAMESPACE__/${NAMESPACE}/g" ../k8s/api-deployment.yaml | kubectl apply $K -f -

# restart
kubectl rollout restart deploy/api -n ${NAMESPACE} $K

sleep 5
kubectl get pods -n ${NAMESPACE} $K | grep api
echo ""
echo "=== Done ==="
