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
# 自有的镜像 tag，**不碰**公共的 `agent-api:latest`（那是 scripts/deploy-api.sh 给
# research / scientific 等按服务部署的 Agent 用的；共用会让两边互相覆盖）。
docker build -f baijiazhengming/Dockerfile.api -t "${REGISTRY}/baijiazhengming-api:latest" .
docker push "${REGISTRY}/baijiazhengming-api:latest"
docker build -f baijiazhengming/Dockerfile.ui -t "${REGISTRY}/baijiazhengming-ui:latest" .
docker push "${REGISTRY}/baijiazhengming-ui:latest"

echo "== 2/5 应用清单（公网流量尚未切换）=="
kubectl apply ${KUBECONFIG_ARG} -f baijiazhengming/k8s.yaml
# 镜像是固定 tag `:latest` + imagePullPolicy: Always —— **apply 不会因此滚动更新**，
# 必须显式重启。实测踩过：清单和 registry 都更新了，UI Pod 还跑着只认识一个人格的
# 旧镜像，于是八个域名里七个仍显示秉笔春秋。
kubectl rollout restart ${KUBECONFIG_ARG} deployment/baijiazhengming-api -n "${NAMESPACE}"
kubectl rollout restart ${KUBECONFIG_ARG} deployment/baijiazhengming-ui -n "${NAMESPACE}"
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-api -n "${NAMESPACE}" --timeout=300s
kubectl rollout status ${KUBECONFIG_ARG} deployment/baijiazhengming-ui -n "${NAMESPACE}" --timeout=300s

echo "== 3/5 自检：八个人格各跑一道真题 =="
kubectl exec ${KUBECONFIG_ARG} -n "${NAMESPACE}" deploy/baijiazhengming-api \
    -- python -m baijiazhengming.selfcheck

echo "== 4/5 更新共享代理的 upstream → ${UPSTREAM} =="
# 八个人格的代理已在 2026-09-28 合并成一个共享实例（oauth2-proxy-baijiazhengming），
# 隧道只喂它。那八个旧的仍留在 ns oauth 里当单域名回滚的退路，但**不再由日常部署
# 渲染/重启** —— 原先是循环它们，结果真正在用的那个反倒不会被更新。
bash "${REPO_ROOT}/oauth/k8s/deploy-agent-proxy.sh" baijiazhengming "${UPSTREAM}"

echo "== 5/5 探活 =="
for slug in "${AGENTS[@]}"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -A 'Mozilla/5.0' -m 15 \
        "https://${slug}-agent.panghuer.top/ready" || true)
    printf '  %-38s %s\n' "${slug}-agent.panghuer.top" "${code}"
done

cat <<'EOF'

完成。当前的回滚口径（八个旧命名空间的 api/ui 已由 retire-legacy-personas.sh 删除）：

- 共享代理/UI 出问题 → 修好它，或把它的 upstream 指回上一个可用地址：
    bash oauth/k8s/deploy-agent-proxy.sh baijiazhengming <upstream>
- 需要按单个域名退回旧服务 → 先用 scripts/deploy-api.sh / deploy-ui.sh 把那个服务搭回来，
  再到 Cloudflare 后台把该域名指回它的 oauth2-proxy-<slug>-agent（那八个代理仍在 ns oauth，
  ConfigMap 也还在，只是当前没流量）。
EOF
