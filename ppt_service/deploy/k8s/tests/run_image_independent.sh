#!/usr/bin/env bash
# Run only against this task's new image and dedicated evidence directory.
set -euo pipefail
case_dir=/root/ppt-service-p1-image-20261023-r1/independent
image=ppt-service-site:p1-20261023-r1
owner=ppt-p1-image-independent-20261023-r1
test_name=ppt-p1-image-independent-tests-20261023-r1
entry_name=ppt-p1-image-independent-entry-20261023-r1
[[ -d "$case_dir/input" && ! -e "$case_dir/output" ]]
for name in "$test_name" "$entry_name"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo 'Dedicated test name already exists; refusing to touch it' >&2
    exit 2
  fi
done
mkdir "$case_dir/output"
chown 10000:10000 "$case_dir/output"
chmod 0700 "$case_dir/output"
cleanup() {
  result=$?
  trap - EXIT
  for name in "$test_name" "$entry_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      actual_owner=$(docker container inspect --format '{{index .Config.Labels "ppt.panghuer.top/owner"}}' "$name")
      if [[ "$actual_owner" != "$owner" ]]; then
        echo 'Unexpected container owner; cleanup refused' >&2
        exit 3
      fi
      docker rm -f "$name" >/dev/null
    fi
  done
  for name in "$test_name" "$entry_name"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      echo 'Owned container remained' >&2
      exit 3
    fi
  done
  printf '{"owned_resources_removed":true,"owner":"%s","runner_exit":%s}\n' "$owner" "$result" > "$case_dir/output/cleanup.json"
  exit "$result"
}
trap cleanup EXIT
image_id=$(docker image inspect --format '{{.Id}}' "$image")
[[ "$image_id" == sha256:374c669d4b556d744a8f1c4ee82b51b3867def29126ecefd6c0d43d0db616314 ]]
docker image inspect --format '{{.Id}} {{.Architecture}} {{.Config.User}} {{json .Config.Entrypoint}}' "$image" > "$case_dir/output/image.txt"
sha256sum "$case_dir/input/test_site.py" "$case_dir/input/test_auth.py" "$case_dir/input/probe_entrypoint.py" > "$case_dir/output/input-sha256.txt"
common=(--pull=never --network none --read-only --user 10000:10000
  --cap-drop ALL --security-opt no-new-privileges --memory 1g --cpus 1 --pids-limit 128
  --label "ppt.panghuer.top/owner=$owner"
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m,uid=10000,gid=10000,mode=0700)
set +e
docker run --rm --name "$test_name" "${common[@]}" \
  --mount "type=bind,src=$case_dir/input,dst=/tests,readonly" \
  --mount "type=bind,src=$case_dir/output,dst=/evidence" \
  --workdir /tmp --env PPT_SITE_TEST_DIST=/opt/ppt-service/web/dist \
  --entrypoint /usr/local/bin/python "$image" -c \
  'import hashlib,json,pathlib,pytest; import ppt_service.server.site as site; root=pathlib.Path("/opt/ppt-service"); assert str(site.__file__)==str(root/"ppt_service/server/site.py"); manifest=root/"build-info/site-inputs.json"; print(json.dumps({"installed_site":str(site.__file__),"site_sha256":hashlib.sha256(pathlib.Path(site.__file__).read_bytes()).hexdigest(),"image_manifest_sha256":hashlib.sha256(manifest.read_bytes()).hexdigest()},sort_keys=True)); raise SystemExit(pytest.main(["/tests/test_site.py","/tests/test_auth.py","-p","no:cacheprovider","--basetemp=/tmp/pytest","--junitxml=/evidence/site-auth.xml","-q"]))' \
  > "$case_dir/output/site-auth.log" 2>&1
pytest_exit=$?
set -e
docker run -d --name "$entry_name" "${common[@]}" \
  --env DATABASE_URL=postgresql+psycopg://unused@127.0.0.1/ppt_p1_no_connection \
  --env DATA_ROOT=/tmp/state \
  --env TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128 \
  "$image" > "$case_dir/output/entrypoint-container.txt"
set +e
docker exec -i "$entry_name" /usr/local/bin/python - \
  < "$case_dir/input/probe_entrypoint.py" > "$case_dir/output/entrypoint.json" 2> "$case_dir/output/entrypoint-probe.log"
entrypoint_exit=$?
set -e
docker logs "$entry_name" > "$case_dir/output/entrypoint.log" 2>&1
printf '{"pytest_exit":%s,"entrypoint_exit":%s,"model_requests":0,"production_ready":false}\n' "$pytest_exit" "$entrypoint_exit" > "$case_dir/output/status.json"
cat "$case_dir/output/status.json"
[[ "$pytest_exit" == 0 && "$entrypoint_exit" == 0 ]]
