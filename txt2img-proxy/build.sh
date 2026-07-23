#!/bin/bash
# ============================================================
#  txt2img-proxy — 构建脚本
#
#  K8s 部署请直接 apply 标准清单:
#    kubectl apply -f k8s/namespace.yaml
#    kubectl apply -f k8s/configmap.yaml
#    kubectl apply -f k8s/secret.yaml
#    kubectl apply -f k8s/deployment.yaml              # API
#    kubectl apply -f k8s/ui-deployment.yaml            # UI
#
#  API Key 通过 Vault + ESO 同步，见 vault/inventory/txt2img-externalsecret.yaml
#
#  用法:
#    bash build.sh                 # 构建 API + UI 镜像并推送
#    bash build.sh api             # 仅构建 API 镜像
#    bash build.sh ui              # 仅构建 UI 镜像 (Gradio)
#    bash build.sh --no-push       # 构建 API + UI 镜像，不推送
# ============================================================
set -euo pipefail

REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
TAG="${TAG:-latest}"
UI_TAG="${UI_TAG:-latest}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

info()  { echo "=== $* ==="; }

do_build_api() {
    info "构建 API 镜像 ${REGISTRY}/txt2img-proxy:${TAG}"
    cd "${SCRIPT_DIR}"
    docker build -f Dockerfile -t "${REGISTRY}/txt2img-proxy:${TAG}" .
    echo "  ✅ API 镜像构建完成"

    if [ "${1:-}" != "--no-push" ]; then
        docker push "${REGISTRY}/txt2img-proxy:${TAG}"
        echo "  ✅ API 镜像已推送"
    fi
}

do_build_ui() {
    info "构建 UI 镜像 ${REGISTRY}/txt2img-ui:${UI_TAG}"
    cd "${SCRIPT_DIR}"
    docker build -f Dockerfile.ui -t "${REGISTRY}/txt2img-ui:${UI_TAG}" .
    echo "  ✅ UI 镜像构建完成"

    if [ "${1:-}" != "--no-push" ]; then
        docker push "${REGISTRY}/txt2img-ui:${UI_TAG}"
        echo "  ✅ UI 镜像已推送"
    fi
}

case "${1:-}" in
    api)
        do_build_api
        ;;
    ui)
        do_build_ui
        ;;
    --no-push)
        do_build_api --no-push
        do_build_ui --no-push
        ;;
    *)
        do_build_api
        do_build_ui
        ;;
esac
