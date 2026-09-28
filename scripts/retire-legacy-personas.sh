#!/usr/bin/env bash
# 删掉八个人格的旧服务与旧命名空间。
#
# ⚠️ 不可逆。跑之前先确认八条链路都已经在新运行时上（deploy.sh 的自检与八个域名的
# /ready 都过了），因为删掉之后回滚就只能靠 scripts/deploy-api.sh / deploy-ui.sh
# 重新搭一套（要重新拉镜像、重新同步 RAG 基线）。
#
# 用法：
#   bash scripts/retire-legacy-personas.sh --yes
#
# 注意：oauth 命名空间里的 oauth2-proxy-<slug>-agent **不删** —— 它们现在指向共享 UI，
# 正是公网入口。这里删的是八个人格自己的命名空间（api/ui 那套）。
set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
    cat <<'EOF'
这会把八个旧命名空间（连同里面的 Deployment / ConfigMap / Service）一起删掉，不可逆。

确认八条链路都在新运行时上（先看 deploy.sh 的自检输出与八个域名的 /ready），然后：
    bash scripts/retire-legacy-personas.sh --yes
EOF
    exit 1
fi

AGENTS=(bingbichunqiu daofaziran fofawubian xiaotanrenjian yimaneili zhenzhuzhida zhongkuifumo zhougongjiemeng)

for slug in "${AGENTS[@]}"; do
    ns="${slug}-agent"
    echo "== 删除命名空间 ${ns} =="
    kubectl delete namespace "${ns}" --ignore-not-found --wait=false
done

cat <<'EOF'

已提交删除（未等待完成，`kubectl get ns` 看进度）。
保留项：ns oauth 里的 oauth2-proxy-<slug>-agent（现在的公网入口）。
EOF
