#!/bin/bash
# ============================================================
#  Panghu Agent — llm-service 令牌同步与重启
#
#  用途：
#    1. 强制 ESO 立即同步 llm-token（从 Vault secret/llm-service/callers 取本调用方那一个键）
#    2. 校验各 namespace 的令牌已就绪
#    3. 重启 api pod 使新 Secret 生效（可选，加 --restart）
#
#  前提：Vault 路径 secret/llm-service/callers 已写入各调用方的 LLM_TOKEN_<CALLER>，
#        且各 namespace 已 apply panghu_agent/k8s/llm-token-externalsecret.yaml
#        （deploy-api.sh 与各服务自己的 deploy 脚本会自动做这一步）。
#
#  用法:
#    bash scripts/sync-llm-token.sh                # 全部 agent
#    bash scripts/sync-llm-token.sh research-agent # 仅指定 agent（可多次传参）
#    bash scripts/sync-llm-token.sh --restart      # 同步 + 校验 + 重启
#
#  说明:
#    - 模型凭据已统一收归集群内 llm-service。各 Agent 不再有 agent-secret / agent-config
#      这层 provider 配置；agent-config 只有 literature-downloader 在用（LITERATURE_* 检索参数），
#      由 literature_downloader/deploy.sh 应用。
#    - 本脚本原名 deploy-agent-config.sh，作用已收敛为上述三项，故改名。
#    - 默认不重启 pod。
# ============================================================
set -euo pipefail

K="--kubeconfig=/etc/kubernetes/super-admin.conf"

# 所有 agent namespace
ALL_NS=(
  research-agent
  scientific-agent
  daofaziran-agent
  fofawubian-agent
  yimaneili-agent
  zhenzhuzhida-agent
  zhongkuifumo-agent
  zhougongjiemeng-agent
  xiaotanrenjian-agent
  bingbichunqiu-agent
  game-review-agent
  literature-downloader
)

# ── 参数解析 ──
DO_RESTART=0
TARGETS=()
for arg in "$@"; do
  case "$arg" in
    --restart) DO_RESTART=1 ;;
    *) TARGETS+=("$arg") ;;
  esac
done

if [ "${#TARGETS[@]}" -gt 0 ]; then
  NS_LIST=("${TARGETS[@]}")
else
  NS_LIST=("${ALL_NS[@]}")
fi

# ============================================================
#  Step 1: 强制 ESO 立即同步 llm-token
# ============================================================
echo "===== Step 1: 强制 ESO 同步 llm-token ====="
for ns in "${NS_LIST[@]}"; do
  kubectl annotate externalsecret llm-token -n "$ns" \
    force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  echo "  ${ns}/llm-token force-sync"
done
# game-review-agent 额外同步 game-auth
if [[ " ${NS_LIST[*]} " =~ " game-review-agent " ]]; then
  kubectl annotate externalsecret game-auth -n game-review-agent \
    force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  echo "  game-review-agent/game-auth force-sync"
fi

# ============================================================
#  Step 2: 校验令牌已就绪
# ============================================================
echo ""
echo "  等待 ESO 同步…"
sleep 5
MISSING_SECRET_NS=()
for ns in "${NS_LIST[@]}"; do
  key=$(kubectl get secret llm-token -n "$ns" -o jsonpath='{.data.LLM_SERVICE_TOKEN}' 2>/dev/null || true)
  if [ -n "$key" ]; then
    echo "  ${ns}/llm-token: ✅ LLM_SERVICE_TOKEN 已同步"
  else
    echo "  ${ns}/llm-token: ⚠️ 未找到 LLM_SERVICE_TOKEN（检查 Vault 路径 secret/llm-service/callers"
    echo "     里有没有本调用方的 LLM_TOKEN_* 键，以及本 namespace 是否已 apply k8s/llm-token-externalsecret.yaml）"
    MISSING_SECRET_NS+=("$ns")
  fi
done

if [ "${#MISSING_SECRET_NS[@]}" -gt 0 ]; then
  echo ""
  echo "ERROR: 以下 namespace 缺少 llm-service 令牌，停止部署且不重启 API："
  printf '  - %s\n' "${MISSING_SECRET_NS[@]}"
  echo "请先写入 Vault 并等待 ExternalSecret Ready=True，然后重新运行本脚本。"
  exit 1
fi

# ============================================================
#  Step 3: 重启 api pod（可选）
# ============================================================
if [ "$DO_RESTART" -eq 1 ]; then
  echo ""
  echo "===== Step 3: 重启 api pods ====="
  for ns in "${NS_LIST[@]}"; do
    echo "  rollout restart ${ns}/api"
    kubectl rollout restart deployment api -n "$ns" $K
  done
  echo ""
  echo "  等待 rollout 完成…"
  for ns in "${NS_LIST[@]}"; do
    kubectl rollout status deployment api -n "$ns" --timeout=180s $K >/dev/null 2>&1 && \
      echo "  ${ns}/api: ✅ 就绪" || echo "  ${ns}/api: ⏳ 仍在滚动"
  done
else
  echo ""
  echo "（跳过重启。如需重启 api pod，加 --restart 参数）"
fi

echo ""
echo "=== Done ==="
