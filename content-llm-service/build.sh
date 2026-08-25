#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE="${REGISTRY}/panghu-content-llm:${IMAGE_TAG}"
docker build -f "${ROOT_DIR}/content-llm-service/Dockerfile" -t "${IMAGE}" "${ROOT_DIR}"
docker push "${IMAGE}"
