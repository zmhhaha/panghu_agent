#!/usr/bin/env bash
# Run on the authorized ARM64 Docker build host. No model credential or network.
set -Eeuo pipefail
IMAGE="${PPT_WORKER_IMAGE:-ppt-service-worker:arm64-c350bc31-verify}"
image_id="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Invalid image identity' >&2; exit 1; }
[[ "$(docker image inspect "$image_id" --format '{{.Architecture}}')" == arm64 ]]
base=/root/ppt-service-sample/probes
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
root="$base/$stamp"
[[ ! -e "$root" ]] || { echo 'Probe path already exists' >&2; exit 1; }
install -d -m 0700 "$base" "$root"
for kind in home project result inputs; do
  install -d -m 0750 -o 10000 -g 10000 "$root/$kind"
done
name="ppt-service-probe-$stamp"
docker create --name "$name" --network none --read-only \
  --user 10000:10000 --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1 --memory 2g --pids-limit 256 --shm-size 256m \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m \
  --mount "type=bind,src=$root/home,dst=/work/home" \
  --mount "type=bind,src=$root/project,dst=/work/project" \
  --mount "type=bind,src=$root/result,dst=/work/result" \
  --mount "type=bind,src=$root/inputs,dst=/work/inputs,readonly" \
  "$image_id" --probe-only --deadline 60 > "$root/container-id.txt"
set +e
docker start -a "$name"
start_status=$?
set -e
docker inspect "$name" --format '{{json .State}}' > "$root/container-state.json"
printf 'Probe name: %s\nEvidence directory: %s\n' "$name" "$root"
exit "$start_status"
