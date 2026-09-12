#!/bin/bash
# ============================================================
#  Panghu Agent — 各 namespace 的 Secret 同步与重启
#
#  用途：
#    1. apply 各 agent 的 ExternalSecret（从 Vault 同步 Secret）
#    2. 强制 ESO 立即同步
#    3. 校验 llm-service 令牌（llm-token / LLM_SERVICE_TOKEN）已就绪
#    4. 重启 api pod 使新 Secret 生效（可选）
#
#  前提：Vault 路径 secret/data/llm-service/auth 已写入 LLM_SERVICE_TOKEN
#
#  用法:
#    bash deploy-agent-config.sh                # 全部 agent
#    bash deploy-agent-config.sh research-agent # 仅指定 agent（可多次传参）
#
#  说明:
#    - 模型凭据已收归集群内 llm-service。各 Agent 的 agent-secret 不再需要 provider key，
#      本脚本也不再写 PROVIDER / DEEPSEEK_* 到 agent-config（那是迁移前的旧配置）。
#    - agent-config 现在只有 literature-downloader 在用（LITERATURE_* 检索参数），
#      由 literature_downloader/deploy.sh 负责应用。
#    - 默认不重启 pod，加 --restart 才执行 rollout restart。
# ============================================================
set -euo pipefail

K="--kubeconfig=/etc/kubernetes/super-admin.conf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ExternalSecret yaml 目录：优先从脚本相对路径找，其次 /tmp（上传场景）
if [ -d "${SCRIPT_DIR}/../../vault/inventory" ]; then
  ES_DIR="${SCRIPT_DIR}/../../vault/inventory"
elif [ -d "${SCRIPT_DIR}/../vault/inventory" ]; then
  ES_DIR="${SCRIPT_DIR}/../vault/inventory"
elif [ -d "/tmp" ]; then
  ES_DIR="/tmp"
else
  echo "错误：找不到 ExternalSecret yaml 目录，请把 *-externalsecret.yaml 放到脚本同目录或 /tmp"
  exit 1
fi

# 所有 agent namespace（不含 game-review 之外的专属）
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
#  Step 1: apply ExternalSecret（extract 整路径提取）
# ============================================================
echo "===== Step 1: apply ExternalSecret ====="
for ns in "${NS_LIST[@]}"; do
  file="${ES_DIR}/${ns}-externalsecret.yaml"
  if [ -f "$file" ]; then
    echo "  apply ${ns}-externalsecret.yaml"
    kubectl apply $K -f "$file"
  else
    echo "  [skip] ${file} 不存在"
  fi
done

# ============================================================
#  Step 2（已移除）：agent-config 不再由本脚本管理
#
#  迁移到 llm-service 后，PROVIDER / DEEPSEEK_* / OPENAI_* 已无人读取，
#  部署模板也不再 envFrom: agent-config。agent-config 目前只有
#  literature-downloader 需要（LITERATURE_* 检索参数），由
#  literature_downloader/deploy.sh 应用。
#
#  注意：各 namespace 里遗留的 PROVIDER / DEEPSEEK_* 键不会自动删除，属惰性残留；
#  如需清理，kubectl delete configmap agent-config -n <ns> 后重新部署即可。
# ============================================================

# ============================================================
#  Step 3: 强制 ESO 立即同步
# ============================================================
echo ""
echo "===== Step 3: 强制 ESO 同步 ====="
for ns in "${NS_LIST[@]}"; do
  for es in agent-secret llm-token; do
    kubectl annotate externalsecret "$es" -n "$ns" \
      force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  done
  echo "  ${ns}: agent-secret / llm-token force-sync"
done
# game-review-agent 额外同步 game-auth
if [[ " ${NS_LIST[*]} " =~ " game-review-agent " ]]; then
  kubectl annotate externalsecret game-auth -n game-review-agent \
    force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  echo "  game-review-agent/game-auth force-sync"
fi

# 等 ESO 同步完成（轮询 llm-token 是否含 LLM_SERVICE_TOKEN）
echo ""
echo "  等待 ESO 同步…"
sleep 5
MISSING_SECRET_NS=()
for ns in "${NS_LIST[@]}"; do
  key=$(kubectl get secret llm-token -n "$ns" -o jsonpath='{.data.LLM_SERVICE_TOKEN}' 2>/dev/null || true)
  if [ -n "$key" ]; then
    echo "  ${ns}/llm-token: ✅ LLM_SERVICE_TOKEN 已同步"
  else
    echo "  ${ns}/llm-token: ⚠️ 未找到 LLM_SERVICE_TOKEN（检查 Vault 路径 secret/llm-service/auth，"
    echo "     以及本 namespace 是否已 apply k8s/llm-token-externalsecret.yaml）"
    MISSING_SECRET_NS+=("$ns")
  fi
done

if [ "${#MISSING_SECRET_NS[@]}" -gt 0 ]; then
  echo ""
  echo "ERROR: 以下 namespace 缺少 llm-service 令牌，停止部署且不重启 API："
  printf '  - %s\n' "${MISSING_SECRET_NS[@]}"
  echo "请先写入对应 Vault 路径并等待 ExternalSecret Ready=True，然后重新运行本脚本。"
  exit 1
fi

# ============================================================
#  Step 4: 重启 api pod（可选）
# ============================================================
if [ "$DO_RESTART" -eq 1 ]; then
  echo ""
  echo "===== Step 4: 重启 api pods ====="
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
