#!/usr/bin/env bash
# Build the three independent content-agent images.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="${REGISTRY:-arm-cluster-master:5000}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PUSH=false
if [[ "${1:-}" == "--push" ]]; then
    PUSH=true
fi

declare -A IMAGES=(
    [github_trending]="panghu-content-github-trending"
    [international_news]="panghu-content-international-news"
    [meme_collector]="panghu-content-meme-collector"
)

for agent in "${!IMAGES[@]}"; do
    image="${REGISTRY}/${IMAGES[$agent]}:${IMAGE_TAG}"
    echo "=== Building ${image} ==="
    docker build -f "${ROOT_DIR}/content_agents/${agent}_agent/Dockerfile" -t "${image}" "${ROOT_DIR}"
    if [[ "${PUSH}" == true ]]; then
        docker push "${image}"
    fi
done

