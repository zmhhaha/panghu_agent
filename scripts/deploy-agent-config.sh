#!/bin/bash
# ============================================================
#  Panghu Agent — 统一部署 DeepSeek LLM 配置
#
#  用途：
#    1. apply 全部 agent 的 ExternalSecret（DeepSeek only + extract）
#    2. 更新各 namespace 的 agent-config ConfigMap（PROVIDER=deepseek）
#    3. 强制 ESO 立即同步 Vault → Secret
#    4. 重启 api pod 使新环境变量生效
#
#  前提：Vault 各路径 secret/data/<ns>/api 已写入 DeepSeek 凭据
#        （DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL）
#
#  用法:
#    bash deploy-agent-config.sh                # 全部 agent
#    bash deploy-agent-config.sh research-agent # 仅指定 agent（可多次传参）
#
#  说明:
#    - game-review-agent 用专属 deploy-api.sh 部署（会自建 ConfigMap），
#      本脚本只同步其 ExternalSecret 和重启 pod。
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
#  Step 2: 更新 agent-config ConfigMap（PROVIDER=deepseek）
# ============================================================
echo ""
echo "===== Step 2: 更新 agent-config ConfigMap ====="
for ns in "${NS_LIST[@]}"; do
  # game-review-agent 由 deploy-api.sh 自动创建，此处仍幂等执行（create+apply 覆盖）
  echo "  ${ns}: PROVIDER=deepseek, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL"
  kubectl create configmap agent-config -n "$ns" \
    --from-literal=PROVIDER=deepseek \
    --from-literal=DEEPSEEK_BASE_URL=https://api.deepseek.com \
    --from-literal=DEEPSEEK_MODEL=deepseek-v4-flash \
    --from-literal=LITERATURE_LLM_ENABLED=true \
    --from-literal=LITERATURE_LLM_TIMEOUT=30 \
    --from-literal=LITERATURE_LLM_MAX_CANDIDATES=40 \
    --dry-run=client -o yaml $K | kubectl apply $K -f -
done

# ============================================================
#  Step 3: 强制 ESO 立即同步
# ============================================================
echo ""
echo "===== Step 3: 强制 ESO 同步 ====="
for ns in "${NS_LIST[@]}"; do
  kubectl annotate externalsecret agent-secret -n "$ns" \
    force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  echo "  ${ns}/agent-secret force-sync"
done
# game-review-agent 额外同步 game-auth
if [[ " ${NS_LIST[*]} " =~ " game-review-agent " ]]; then
  kubectl annotate externalsecret game-auth -n game-review-agent \
    force-sync=$(date +%s) --overwrite $K >/dev/null 2>&1 || true
  echo "  game-review-agent/game-auth force-sync"
fi

# 等 ESO 同步完成（轮询 Secret 是否含 DEEPSEEK_API_KEY）
echo ""
echo "  等待 ESO 同步…"
sleep 5
MISSING_SECRET_NS=()
for ns in "${NS_LIST[@]}"; do
  key=$(kubectl get secret agent-secret -n "$ns" -o jsonpath='{.data.DEEPSEEK_API_KEY}' 2>/dev/null || true)
  if [ -n "$key" ]; then
    echo "  ${ns}/agent-secret: ✅ DEEPSEEK_API_KEY 已同步"
  else
    echo "  ${ns}/agent-secret: ⚠️ 未找到 DEEPSEEK_API_KEY（检查 Vault 路径 secret/data/${ns}/api）"
    MISSING_SECRET_NS+=("$ns")
  fi
done

if [ "${#MISSING_SECRET_NS[@]}" -gt 0 ]; then
  echo ""
  echo "ERROR: 以下 namespace 缺少 DEEPSEEK_API_KEY，停止部署且不重启 API："
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
