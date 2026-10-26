#!/usr/bin/env bash
# Build only in a new dedicated directory containing package_site.py's context.tar.
set -euo pipefail
case_dir=$(realpath -- "${1:?new case directory required}")
output_image=${2:?new local output tag required}
[[ "$output_image" =~ ^ppt-service-site:p1-[a-z0-9-]+$ ]] || { echo 'Use a dedicated ppt-service-site:p1-* tag' >&2; exit 2; }
[[ -f "$case_dir/context.tar" && ! -e "$case_dir/context" ]] || { echo 'Need context.tar and no context directory' >&2; exit 2; }
if docker image inspect "$output_image" >/dev/null 2>&1; then echo 'Output image already exists' >&2; exit 2; fi
dependencies=ppt-service-api:parse-dev-20261022
expected_dependencies=sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7
runtime=python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
expected_runtime=sha256:2bdb5454ee915d04c4c592050b402aa83eff9b3b86597574961e5b95457a560c
[[ $(docker image inspect --format '{{.Id}}' "$dependencies") == "$expected_dependencies" ]]
[[ $(docker image inspect --format '{{.Id}}' "$runtime") == "$expected_runtime" ]]
[[ $(docker image inspect --format '{{.Architecture}}' "$runtime") == arm64 ]]
mkdir "$case_dir/context"
tar --extract --file "$case_dir/context.tar" --directory "$case_dir/context" --no-same-owner
sha256sum "$case_dir/context.tar" > "$case_dir/context-tar.sha256"
DOCKER_BUILDKIT=0 docker build --pull=false --network=none \
  --build-arg "SITE_DEPENDENCY_IMAGE=$dependencies" --build-arg "PYTHON_RUNTIME_IMAGE=$runtime" \
  -t "$output_image" -f "$case_dir/context/Dockerfile.site" "$case_dir/context" 2>&1 | tee "$case_dir/build.log"
docker image inspect --format '{{.Id}} {{.Architecture}} {{.Config.User}} {{json .Config.Entrypoint}}' "$output_image" > "$case_dir/image.txt"
docker run --rm --pull=never --network none --read-only --user 10000:10000 \
  --cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1 --pids-limit 128 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,uid=10000,gid=10000 \
  --entrypoint /usr/local/bin/python "$output_image" /opt/ppt-service/verify_image.py \
  | tee "$case_dir/image-verification.json"
echo 'Built and validated locally; no registry push, Kubernetes deployment or model request.'
