#!/bin/bash
# ============================================================
#  txt2img-proxy — 构建脚本（仅镜像构建）
#
#  K8s 部署请直接 apply 标准清单:
#    kubectl apply -f k8s/namespace.yaml
#    kubectl apply -f k8s/configmap.yaml
#    kubectl apply -f k8s/deployment.yaml
#
#  API Key 通过 Vault + ESO 同步，见 vault/inventory/txt2img-externalsecret.yaml
#
#  用法:
#    bash build.sh              # 构建镜像并推送
#    bash build.sh --no-push    # 仅构建不推送
# ============================================================
set -euo pipefail

REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
TAG="${TAG:-latest}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info()  { echo "=== $* ==="; }

do_build() {
    info "构建镜像 ${REGISTRY}/txt2img-proxy:${TAG}"
    cd "${SCRIPT_DIR}"
    docker build -f Dockerfile -t "${REGISTRY}/txt2img-proxy:${TAG}" .
    echo "  ✅ 构建完成"

    if [ "${1:-}" != "--no-push" ]; then
        docker push "${REGISTRY}/txt2img-proxy:${TAG}"
        echo "  ✅ 已推送"
    fi
}

case "${1:-}" in
    --no-push) do_build --no-push ;;
    *)         do_build ;;
esac
