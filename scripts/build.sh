#!/bin/bash
# ============================================================
#  构建脚本（内网 ARM64 环境）
#  依赖 arm-cluster-master:5000/base:latest（已配国内源）
# ============================================================
#  用法:
#    ./scripts/build.sh api             # 构建 API 镜像
#    ./scripts/build.sh ui              # 构建 UI 镜像
#    ./scripts/build.sh api --push      # 构建 + 推送
#    ./scripts/build.sh ui --arm-only   # 仅构建 ARM64
# ============================================================
set -euo pipefail

REGISTRY="${REGISTRY:-arm-cluster-master:5000}"

cd "$(dirname "$0")/.."

TARGET="${1:-}"
ACTION="${2:-}"

usage() {
  echo "用法: $0 {api|ui} [--push|--arm-only]"
  exit 1
}

case "$TARGET" in
  api)
    IMAGE_NAME="${IMAGE_NAME:-agent-api}"
    DOCKERFILE="Dockerfile.api"
    PORT="8000"
    ;;
  ui)
    IMAGE_NAME="${IMAGE_NAME:-agent-ui}"
    DOCKERFILE="Dockerfile.ui"
    PORT="7860"
    ;;
  *) usage ;;
esac

IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

case "$ACTION" in
  --push)
    echo "=== 多架构构建 + 推送: ${FULL_IMAGE} ==="
    docker buildx build \
      --build-arg REGISTRY="${REGISTRY}" \
      --platform linux/amd64,linux/arm64 \
      -f "${DOCKERFILE}" \
      -t "${FULL_IMAGE}" \
      --push \
      .
    echo "完成! 拉取: docker pull ${FULL_IMAGE}"
    ;;

  --arm-only)
    echo "=== ARM64 构建: ${FULL_IMAGE}-arm64 ==="
    docker buildx build \
      --build-arg REGISTRY="${REGISTRY}" \
      --platform linux/arm64 \
      -f "${DOCKERFILE}" \
      -t "${FULL_IMAGE}-arm64" \
      --load \
      .
    echo "完成! 镜像: ${FULL_IMAGE}-arm64"
    ;;

  *)
    echo "=== 本地构建: ${FULL_IMAGE} ==="
    docker build \
      --build-arg REGISTRY="${REGISTRY}" \
      -f "${DOCKERFILE}" \
      -t "${FULL_IMAGE}" .
    echo "完成! 运行: docker run -d -p ${PORT}:${PORT} ${FULL_IMAGE}"
    ;;
esac
