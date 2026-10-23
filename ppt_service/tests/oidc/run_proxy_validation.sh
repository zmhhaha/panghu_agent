#!/usr/bin/env bash
# One owned temporary runner; fixed local images, no network or production data.
set -euo pipefail
case_dir=${1:?absolute dedicated case directory required}
[[ "$case_dir" == /root/ppt-service-p2-oidc-20261023-r* ]]
[[ -d "$case_dir/input" && ! -e "$case_dir/output" ]]
site_id=${PPT_SITE_IMAGE_ID:?set the rebuilt email-admission site image config ID explicitly}
[[ "$site_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'PPT_SITE_IMAGE_ID must be an immutable sha256 image config ID' >&2; exit 2; }
proxy_id=sha256:c410cd2f0b69eadd73498ede45579e847177e1fd45947fe3500091409c532867
owner=ppt-p2-oidc-20261023
extract_name=ppt-p2-oidc-extract-20261023
test_name=ppt-p2-oidc-test-20261023
for name in "$extract_name" "$test_name"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo 'Dedicated name exists; refusing to touch it' >&2
    exit 2
  fi
done
[[ "$(docker image inspect --format '{{.Id}}' "$site_id")" == "$site_id" ]]
[[ "$(docker image inspect --format '{{.Architecture}}' "$site_id")" == arm64 ]]
docker image inspect "$proxy_id" >/dev/null
mkdir "$case_dir/output" "$case_dir/bin"
chown 10000:10000 "$case_dir/output"
chmod 0700 "$case_dir/output"
cleanup() {
  result=$?
  trap - EXIT
  for name in "$extract_name" "$test_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      actual_owner=$(docker container inspect --format '{{index .Config.Labels "ppt.panghuer.top/owner"}}' "$name")
      [[ "$actual_owner" == "$owner" ]] || exit 3
      docker rm -f "$name" >/dev/null
    fi
  done
  for name in "$extract_name" "$test_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      echo 'Owned container remained' >&2
      exit 3
    fi
  done
  printf '{"owned_resources_removed":true,"owner":"%s","runner_exit":%s,"network":"none","published_ports":0}\n' "$owner" "$result" > "$case_dir/output/cleanup.json"
  exit "$result"
}
trap cleanup EXIT
docker image inspect --format '{{.Id}} {{.Architecture}} {{json .RepoDigests}}' "$proxy_id" > "$case_dir/output/proxy-image.txt"
docker image inspect --format '{{.Id}} {{.Architecture}}' "$site_id" > "$case_dir/output/site-image.txt"
docker create --name "$extract_name" --network none --label "ppt.panghuer.top/owner=$owner" "$proxy_id" >/dev/null
docker cp "$extract_name:/bin/oauth2-proxy" "$case_dir/bin/oauth2-proxy"
docker rm "$extract_name" >/dev/null
chmod 0555 "$case_dir/bin/oauth2-proxy"
sha256sum "$case_dir/bin/oauth2-proxy" "$case_dir/input/synthetic_oidc.py" "$case_dir/input/oauth2-proxy.cfg" > "$case_dir/output/input-sha256.txt"
set +e
docker run --rm --name "$test_name" --pull=never --network none --read-only \
  --user 10000:10000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --cpus 1 --pids-limit 128 --label "ppt.panghuer.top/owner=$owner" \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m,uid=10000,gid=10000,mode=0700 \
  --mount "type=bind,src=$case_dir/input,dst=/tests,readonly" \
  --mount "type=bind,src=$case_dir/bin,dst=/probe-bin,readonly" \
  --mount "type=bind,src=$case_dir/output,dst=/evidence" \
  --entrypoint /usr/local/bin/python "$site_id" /tests/synthetic_oidc.py \
  --template /tests/oauth2-proxy.cfg --binary /probe-bin/oauth2-proxy --output /evidence/summary.json \
  > "$case_dir/output/checks.log" 2>&1
test_exit=$?
set -e
printf '{"protocol_test_exit":%s,"persistent_database_used":false,"models_called":0,"casdoor_client_used":false,"production_ready":false}\n' "$test_exit" > "$case_dir/output/status.json"
cat "$case_dir/output/status.json"
[[ "$test_exit" == 0 ]]
