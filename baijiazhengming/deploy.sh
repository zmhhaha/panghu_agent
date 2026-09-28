#!/usr/bin/env bash
# 百家争鸣：把八个人格一次性部署到共享运行时。
#
# 顺序是有讲究的：
#   1. 构建 + 推送镜像
#   2. apply 框架清单（新的 api/ui 起来，**此时公网还指着旧服务**）
#   3. 自检：八个人格各跑一道真题 —— 不过就停在这里，什么都不切
#   4. 从 oauth 模板渲染八个代理、upstream 指向共享 UI   ← 这一步才动流量
#   5. 八个域名逐个探活
#
# 旧服务与旧命名空间**不动**，回滚就是把代理的 upstream 指回去（见末尾）。
# 确认稳定后再跑 scripts/retire-legacy-personas.sh。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"      # panghu_agent
REPO_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"        # armbianbegin —— oauth 模板在这里
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
KUBECONFIG_ARG="${KUBECONFIG_ARG:---kubeconfig=/etc/kubernetes/super-admin.conf}"
NAMESPACE="${NAMESPACE:-baijiazhengming}"
UPSTREAM="${UPSTREAM:-http://baijiazhengming-ui.${NAMESPACE}.svc.cluster.local:7860}"

AGENTS=(bingbichunqiu daofaziran fofawubian xiaotanrenjian yimaneili zhenzhuzhida zhongkuifumo zhougongjiemeng)

cd "${ROOT_DIR}"

echo "== 1/5 构建镜像 =="
docker build -f Dockerfile.api -t "${REGISTRY}/agent-api:latest" .
docker push "${REGISTRY}/agent-api:latest"
docker build -f baijiazhengming/Dockerfile.ui -t "${REGISTRY}/baijiazhengming-ui:latest" .
docker push "${REGISTRY}/baijiazhengming-ui:latest"

echo "== 2/5 应用清单（公网流量尚未切换）=="
kubectl apply ${KUBECONFIG_ARG} -f baijiazhengming/k8s.yaml
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-api -n "${NAMESPACE}" --timeout=300s
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-ui -n "${NAMESPACE}" --timeout=300s

echo "== 3/5 自检：八个人格各跑一道真题 =="
kubectl exec ${KUBECONFIG_ARG} -n "${NAMESPACE}" deploy/baijiazhengming-api \
    -- python -m baijiazhengming.selfcheck

echo "== 4/5 切换八个代理的 upstream → ${UPSTREAM} =="
for slug in "${AGENTS[@]}"; do
    bash "${REPO_ROOT}/oauth/k8s/deploy-agent-proxy.sh" "${slug}-agent" "${UPSTREAM}"
done

echo "== 5/5 探活 =="
for slug in "${AGENTS[@]}"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -A 'Mozilla/5.0' -m 15 \
        "https://${slug}-agent.panghuer.top/ready" || true)
    printf '  %-38s %s\n' "${slug}-agent.panghuer.top" "${code}"
done

cat <<'EOF'

完成。旧服务仍在跑，回滚就是把某个代理的 upstream 指回去（不带第二个参数 = 旧值）：
  bash oauth/k8s/deploy-agent-proxy.sh <slug>-agent

确认八个域名都正常、且登录后各是各自的人格之后，再执行：
  bash panghu_agent/scripts/retire-legacy-personas.sh
EOF
