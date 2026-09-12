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
#    - 模板 k8s/api-deployment.yaml，__NAMESPACE__ / __AGENT__ / __LLM_MODEL__ 占位
#    - llm-service 档位：research / scientific 带检索工具，走 chat-tools（trusted）；
#      其余（用户写 prompt 的对话型 Agent）走 chat-guarded。可用环境变量 LLM_MODEL 覆盖。
# ============================================================
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"

NAME="${1:-research_agent}"
NAMESPACE="${NAME//_/-}"   # namespace
AGENT="${NAME%_agent}"     # 调用者名（与 RAG 的 RAG_TOKEN_<CALLER> 对应）
VAULT_TOKEN_KEY="RAG_TOKEN_$(printf '%s' "$AGENT" | tr 'a-z-' 'A-Z_')"
K="--kubeconfig=/etc/kubernetes/super-admin.conf"
SRC="../app/api/${NAME}.py"

# llm-service 别名：guarded 档禁 tools/response_format，带工具的 Agent 必须用 trusted 档
case "$AGENT" in
    research|scientific) LLM_MODEL="${LLM_MODEL:-chat-tools}" ;;
    *)                   LLM_MODEL="${LLM_MODEL:-chat-guarded}" ;;
esac

echo "=== Deploying API: ${NAME} (namespace: ${NAMESPACE}) ==="

# build + push image
echo "=== Building image ==="
cd ..
docker build -f Dockerfile.api -t arm-cluster-master:5000/agent-api:latest .
docker push arm-cluster-master:5000/agent-api:latest
cd scripts

# ensure namespace
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml $K | kubectl apply $K -f -

# 注：原先这里还会创建 agent-config（PROVIDER / CUSTOM_*）和 agent-secret（provider key）。
# 迁移到 llm-service 后两者都不再需要——模型凭据只存在于 llm-service 自己：
#   agent-config 只剩 literature-downloader 用（LITERATURE_*），由它的 deploy.sh 应用；
#   provider key 由各 namespace 的 Vault ExternalSecret 管理，本脚本不碰。

# ConfigMap (agent.py)
kubectl create configmap api-agent -n ${NAMESPACE} \
    --from-file=agent.py="${SRC}" \
    --dry-run=client -o yaml $K | kubectl apply $K -f -

# RAG 调用令牌：只取本 Agent 那一个 Vault 键（ExternalSecret 每命名空间一份）
# research / scientific / game_review 没有 knowledge.md，Vault 里也没有对应的 RAG_TOKEN_*，
# 应用了只会留下一个永远 not-ready 的 ExternalSecret（见 README 踩坑 #4），故默认跳过。
# 需要强制时设 RAG_CALLER=1（例如将来给它们补了 knowledge.md 和 Vault 键）。
NO_RAG_CALLER=false
case "$AGENT" in
    research|scientific|game_review) NO_RAG_CALLER=true ;;
esac
case "${RAG_CALLER:-}" in
    1|true|yes) NO_RAG_CALLER=false ;;
    0|false|no) NO_RAG_CALLER=true ;;
esac

if [ "$NO_RAG_CALLER" = "true" ]; then
    echo "  [skip] ${AGENT} 不是 RAG 调用方，跳过 rag-token ExternalSecret"
else
    sed -e "s/__NAMESPACE__/${NAMESPACE}/g" \
        -e "s/__VAULT_TOKEN_KEY__/${VAULT_TOKEN_KEY}/g" \
        ../k8s/rag-token-externalsecret.yaml | kubectl apply $K -f -
fi

# llm-service 调用令牌（每个命名空间一份，与 RAG 同源）
sed "s/__NAMESPACE__/${NAMESPACE}/g" \
    ../k8s/llm-token-externalsecret.yaml | kubectl apply $K -f -

# apply K8s
echo "  llm-service 别名: ${LLM_MODEL}"
sed -e "s/__NAMESPACE__/${NAMESPACE}/g" \
    -e "s/__AGENT__/${AGENT}/g" \
    -e "s|__LLM_MODEL__|${LLM_MODEL}|g" \
    ../k8s/api-deployment.yaml | kubectl apply $K -f -

# restart
kubectl rollout restart deploy/api -n ${NAMESPACE} $K

sleep 5
kubectl get pods -n ${NAMESPACE} $K | grep api
echo ""
echo "=== Done ==="
