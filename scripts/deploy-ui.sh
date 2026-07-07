#!/bin/bash
# ============================================================
#  Gradio UI 部署脚本 — ConfigMap 注入 agent.py + K8s apply
#
#  用法:
#    bash scripts/deploy-ui.sh research_agent            # 部署研究助手
#    bash scripts/deploy-ui.sh scientific_agent           # 部署科研综述
#
#  约定:
#    - 源码 app/ui/<name>.py → ConfigMap key agent.py
#    - 模板 k8s/ui-deployment.yaml，__NAMESPACE__ placehold
#    - 每个 namespace 独立的 ui-agent ConfigMap
set -e
script_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$script_dir"

NAME="${1:-research_agent}"
NAMESPACE="${NAME//_/-}"   # namespace
K="--kubeconfig=/etc/kubernetes/super-admin.conf"
SRC="../app/ui/${NAME}.py"
TMPL="../k8s/ui-deployment.yaml"

echo "=== Deploying ${NAME} (namespace: ${NAMESPACE}) ==="

# ensure namespace
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml $K | kubectl apply $K -f -

# ConfigMap (agent.py)
kubectl create configmap ui-agent -n ${NAMESPACE} \
    --from-file=agent.py="${SRC}" \
    --dry-run=client -o yaml $K | kubectl apply $K -f -

# apply K8s resources
sed "s/__NAMESPACE__/${NAMESPACE}/g" "$TMPL" | kubectl apply $K -f -

# restart
kubectl rollout restart deploy/ui -n ${NAMESPACE} $K

sleep 5
kubectl get pods -n ${NAMESPACE} $K | grep ui
echo ""
echo "=== Done ==="
