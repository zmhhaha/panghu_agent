#!/bin/bash
# ============================================================
#  构建脚本（内网 ARM64 环境）
#  依赖 arm-cluster-master:5000/base:latest（已配国内源）
# ============================================================
#  用法:
#    ./scripts/build.sh              # 本地构建（当前架构）
#    ./scripts/build.sh --push       # 构建多架构 + 推送
#    ./scripts/build.sh --arm-only   # 仅构建 ARM64
# ============================================================
set -euo pipefail

REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_NAME="${IMAGE_NAME:-panghu-agent}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

cd "$(dirname "$0")/.."

case "${1:-}" in
  --push)
    echo "=== 多架构构建 + 推送: ${FULL_IMAGE} ==="
    docker buildx build \
      --build-arg REGISTRY="${REGISTRY}" \
      --platform linux/amd64,linux/arm64 \
      --tag "${FULL_IMAGE}" \
      --push \
      .
    echo "完成! 拉取: docker pull ${FULL_IMAGE}"
    ;;

  --arm-only)
    echo "=== ARM64 构建: ${FULL_IMAGE}-arm64 ==="
    docker buildx build \
      --build-arg REGISTRY="${REGISTRY}" \
      --platform linux/arm64 \
      --tag "${FULL_IMAGE}-arm64" \
      --load \
      .
    echo "完成! 镜像: ${FULL_IMAGE}-arm64"
    ;;

  *)
    echo "=== 本地构建: ${FULL_IMAGE} ==="
    docker build \
      --build-arg REGISTRY="${REGISTRY}" \
      -t "${FULL_IMAGE}" .
    echo "完成! 运行: docker run -d -p 8000:8000 ${FULL_IMAGE}"
    ;;
esac
