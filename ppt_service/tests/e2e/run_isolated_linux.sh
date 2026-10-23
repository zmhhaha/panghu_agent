#!/usr/bin/env bash
# Run on a Linux Docker host with the two images already present. No pulls,
# network, production database, host credentials, or Docker socket in test apps.
set -euo pipefail
case_dir=$(realpath -- "${1:?Usage: run_isolated_linux.sh NEW_CASE_DIR_WITH_SOURCE_TAR}")
test -f "$case_dir/source.tar"
test ! -e "$case_dir/source"
test ! -e "$case_dir/evidence"
api_image=sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7
pg_image=sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368
docker image inspect "$api_image" "$pg_image" > /dev/null
run_id="ppt-p5-$(date -u +%Y%m%d%H%M%S)-$$"
socket_volume="$run_id-socket"
pg_container="$run_id-postgres"
helper_container="$run_id-init"
test_containers=()
owned_container_remove() {
    local name="$1" label
    label=$(docker inspect --format '{{index .Config.Labels "ppt.p5.owner"}}' "$name" 2>/dev/null || true)
    if [ "$label" = "$run_id" ]; then docker rm -f "$name" >/dev/null; fi
}
cleanup() {
    local name label
    for name in "${test_containers[@]}" "$pg_container" "$helper_container"; do owned_container_remove "$name"; done
    label=$(docker volume inspect --format '{{index .Labels "ppt.p5.owner"}}' "$socket_volume" 2>/dev/null || true)
    if [ "$label" = "$run_id" ]; then docker volume rm "$socket_volume" >/dev/null; fi
    # A Docker daemon/inspection failure must never become a clean receipt.
    local remaining
    if ! remaining=$(docker ps -aq --filter "label=ppt.p5.owner=$run_id"); then return 1; fi
    if [ -n "$remaining" ]; then return 1; fi
    if ! remaining=$(docker volume ls -q --filter "label=ppt.p5.owner=$run_id"); then return 1; fi
    if [ -n "$remaining" ]; then return 1; fi
    # This receipt is written only after all owned resource removal succeeds.
    printf '{"owned_resources_removed":true,"run_id":"%s"}\n' "$run_id" > "$case_dir/evidence/cleanup.json"
}
mkdir "$case_dir/source" "$case_dir/evidence"
tar --no-same-owner -xf "$case_dir/source.tar" -C "$case_dir/source"
chmod -R a+rX "$case_dir/source"
chown 10000:10000 "$case_dir/evidence"
trap cleanup EXIT
sha256sum "$case_dir/source.tar" > "$case_dir/evidence/source-tar.sha256"
docker image inspect "$api_image" "$pg_image" --format '{{.Id}} {{.Architecture}}' > "$case_dir/evidence/images.txt"
date -u +%FT%TZ > "$case_dir/evidence/started-at.txt"
docker volume create --label "ppt.p5.owner=$run_id" "$socket_volume" >/dev/null
docker run --pull=never --rm --name "$helper_container" --label "ppt.p5.owner=$run_id" \
  --network none --read-only --cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges \
  --mount "type=volume,source=$socket_volume,target=/pgsocket" \
  --entrypoint sh "$pg_image" -c 'chmod 1777 /pgsocket && chown 70:70 /pgsocket'
docker run --pull=never -d --name "$pg_container" --label "ppt.p5.owner=$run_id" \
  --network none --user 70:70 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 512m --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev,size=384m,uid=70,gid=70,mode=700 \
  --mount "type=volume,source=$socket_volume,target=/pgsocket" \
  --mount "type=volume,source=$socket_volume,target=/var/run/postgresql" \
  -e POSTGRES_DB=ppt_p5_contract -e POSTGRES_HOST_AUTH_METHOD=trust -e PGHOST=/pgsocket \
  "$pg_image" -c listen_addresses= -c unix_socket_directories=/var/run/postgresql -c unix_socket_permissions=0777 > /dev/null
ready=0
for attempt in $(seq 1 30); do
  if docker exec "$pg_container" sh -c 'test "$(cat /proc/1/comm)" = postgres && pg_isready -h /pgsocket -U postgres -d ppt_p5_contract' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then docker logs "$pg_container" > "$case_dir/evidence/postgres-container.log" 2>&1; cat "$case_dir/evidence/postgres-container.log"; exit 2; fi
run_python() {
    local name="$run_id-api-$1" database="$2"
    shift 2
    test_containers+=("$name")
    local env_args=()
    if [ -n "$database" ]; then
      env_args=(-e PPT_E2E_TEST_ONLY=isolated-p5
        -e "PPT_E2E_DATABASE_URL=postgresql+psycopg://postgres@/$database?host=/pgsocket"
        -e "TEST_DATABASE_URL=postgresql+psycopg://postgres@/$database?host=/pgsocket")
    fi
    docker run --pull=never --rm --name "$name" --label "ppt.p5.owner=$run_id" \
      --network none --user 10000:10000 --read-only --cap-drop ALL --security-opt no-new-privileges \
      --pids-limit 128 --memory 768m --cpus 1 \
      --tmpfs /tmp:rw,nosuid,nodev,size=384m,mode=1777 \
      --mount "type=bind,source=$case_dir/source,target=/src,readonly" \
      --mount "type=bind,source=$case_dir/evidence,target=/evidence" \
      --mount "type=volume,source=$socket_volume,target=/pgsocket" \
      -w /src -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 \
      "${env_args[@]}" --entrypoint python "$api_image" "$@"
}
# Preserve every genuine failure and still run both databases.
set +e
run_python sqlite '' -m ppt_service.tests.e2e.run_suite --report /evidence/sqlite.json > "$case_dir/evidence/sqlite.log" 2>&1
sqlite_status=$?
run_python postgres ppt_p5_contract -m ppt_service.tests.e2e.run_suite --report /evidence/postgres.json > "$case_dir/evidence/postgres.log" 2>&1
postgres_status=$?
set -e
docker exec "$pg_container" createdb -h /pgsocket -U postgres ppt_p5_regression
set +e
run_python regression ppt_p5_regression -m pytest \
  ppt_service/tests/server/test_api.py ppt_service/tests/server/test_auth.py \
  ppt_service/tests/server/test_postgres.py ppt_service/tests/control/test_control.py \
  -q -p no:cacheprovider --junitxml=/evidence/regression.xml > "$case_dir/evidence/regression.log" 2>&1
regression_status=$?
set -e
docker exec "$pg_container" createdb -h /pgsocket -U postgres ppt_p5_backup
set +e
run_python seed ppt_p5_backup -m ppt_service.tests.e2e.pg_backup_probe seed --evidence /evidence/pg-backup > "$case_dir/evidence/pg-backup-seed.log" 2>&1
seed_status=$?
set -e
restore_status=99
if [ "$seed_status" = 0 ]; then
  docker exec "$pg_container" pg_dump -h /pgsocket -U postgres -Fc ppt_p5_backup > "$case_dir/evidence/pg-backup.dump"
  docker exec "$pg_container" createdb -h /pgsocket -U postgres ppt_p5_restore
  docker exec -i "$pg_container" pg_restore -h /pgsocket -U postgres --exit-on-error -d ppt_p5_restore < "$case_dir/evidence/pg-backup.dump"
  set +e
  run_python restore ppt_p5_restore -m ppt_service.tests.e2e.pg_backup_probe verify --evidence /evidence/pg-backup > "$case_dir/evidence/pg-backup-verify.log" 2>&1
  restore_status=$?
  set -e
fi
docker logs "$pg_container" > "$case_dir/evidence/postgres-container.log" 2>&1
printf '{"sqlite_exit":%s,"postgres_exit":%s,"regression_exit":%s,"pg_backup_seed_exit":%s,"pg_restore_verify_exit":%s,"model_requests":0}\n' \
  "$sqlite_status" "$postgres_status" "$regression_status" "$seed_status" "$restore_status" > "$case_dir/evidence/status.json"
cat "$case_dir/evidence/status.json"
test "$sqlite_status" = 0 && test "$postgres_status" = 0 && test "$regression_status" = 0 && test "$seed_status" = 0 && test "$restore_status" = 0
