#!/bin/bash
# ============================================================
#  游戏试玩评价 agent — 专属 API 部署脚本
#
#  用法:
#    bash game_review_agent/deploy-api.sh          # 构建 + 部署
#    bash game_review_agent/deploy-api.sh --build-only   # 只构建镜像
#
#  特点：使用独立镜像 game-review-agent-api（含 Playwright/Chromium），
#        不修改共享 agent-api-base，不影响其他 agent。
# ============================================================
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir/.."

NAME="game_review_agent"
NAMESPACE="game-review-agent"
K="--kubeconfig=/etc/kubernetes/super-admin.conf"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE="${REGISTRY}/game-review-agent-api:latest"

echo "=== 构建独立镜像: ${IMAGE} ==="
docker build \
  --build-arg REGISTRY="${REGISTRY}" \
  -f game_review_agent/Dockerfile.api \
  -t "${IMAGE}" .
docker push "${IMAGE}"

if [ "${1:-}" = "--build-only" ]; then
  echo "=== 镜像构建完成，跳过部署 ==="
  exit 0
fi

echo "=== 部署 API (namespace: ${NAMESPACE}) ==="

# ensure namespace
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml $K | kubectl apply $K -f -

# ConfigMap (agent.py)
kubectl create configmap api-agent -n ${NAMESPACE} \
    --from-file=agent.py="app/api/${NAME}.py" \
    --dry-run=client -o yaml $K | kubectl apply $K -f -

# ConfigMap (agent-config: LLM 配置，ESO 不管理 ConfigMap 故手动创建)
# 支持通过环境变量覆盖默认值，例如: PROVIDER=deepseek DEEPSEEK_MODEL=deepseek-v4-flash bash game_review_agent/deploy-api.sh
kubectl create configmap agent-config -n ${NAMESPACE} \
    --from-literal=PROVIDER="${PROVIDER:-deepseek}" \
    --from-literal=DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}" \
    --from-literal=DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-flash}" \
    --from-literal=OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com}" \
    --from-literal=OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}" \
    --dry-run=client -o yaml $K | kubectl apply $K -f -

# apply 专属 deployment
sed "s/__NAMESPACE__/${NAMESPACE}/g" game_review_agent/k8s/api-deployment.yaml | kubectl apply $K -f -

# restart
kubectl rollout restart deploy/api -n ${NAMESPACE} $K

sleep 5
kubectl get pods -n ${NAMESPACE} $K | grep api
echo ""
echo "=== Done ==="
