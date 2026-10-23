#!/usr/bin/env bash
# Opt-in disposable P1 rehearsal. No production config, model, socket in apps,
# TCP networking, image pulls, persistent PG data, or generic cleanup/prune.
set -euo pipefail
case_dir=$(realpath -- "${1:?NEW_CASE_DIR_WITH_SOURCE_TAR}")
test -f "$case_dir/source.tar"
test ! -e "$case_dir/source"
test ! -e "$case_dir/evidence"
api_image=sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7
site_image=ppt-service-site:p1-20261023-r1
pg_image=sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368
docker image inspect "$api_image" "$site_image" "$pg_image" > /dev/null
site_image=$(docker image inspect --format '{{.Id}}' "$site_image")
run_id="ppt-p1-migration-$(date -u +%Y%m%d%H%M%S)-$$"
socket_volume="$run_id-socket"
pg_container="$run_id-postgres"
helper_container="$run_id-init"
test_containers=()
owned_container_remove() {
    local name="$1" label
    label=$(docker inspect --format '{{index .Config.Labels "ppt.p1.migration.owner"}}' "$name" 2>/dev/null || true)
    if [ "$label" = "$run_id" ]; then docker rm -f "$name" > /dev/null; fi
}
cleanup() {
    local name label remaining
    for name in "${test_containers[@]}" "$pg_container" "$helper_container"; do owned_container_remove "$name"; done
    label=$(docker volume inspect --format '{{index .Labels "ppt.p1.migration.owner"}}' "$socket_volume" 2>/dev/null || true)
    if [ "$label" = "$run_id" ]; then docker volume rm "$socket_volume" >/dev/null; fi
    remaining=$(docker ps -aq --filter "label=ppt.p1.migration.owner=$run_id") || return 1
    test -z "$remaining" || return 1
    remaining=$(docker volume ls -q --filter "label=ppt.p1.migration.owner=$run_id") || return 1
    test -z "$remaining" || return 1
    printf '{"owned_resources_removed":true,"run_id":"%s"}\n' "$run_id" > "$case_dir/evidence/cleanup.json"
}
mkdir "$case_dir/source" "$case_dir/evidence"
tar --no-same-owner -xf "$case_dir/source.tar" -C "$case_dir/source"
chmod -R a+rX "$case_dir/source"
trap cleanup EXIT
sha256sum "$case_dir/source.tar" > "$case_dir/evidence/source-tar.sha256"
docker image inspect "$api_image" "$site_image" "$pg_image" --format '{{.Id}} {{.Architecture}}' > "$case_dir/evidence/images.txt"
date -u +%FT%TZ > "$case_dir/evidence/started-at.txt"
tools="$case_dir/source/ppt_service/deploy/k8s/migration"
run_python() {
    local stage="$1" image="$2"
    shift 2
    local name="$run_id-$stage"
    test_containers+=("$name")
    docker run --pull=never --rm --name "$name" --label "ppt.p1.migration.owner=$run_id" \
      --network none --user 0:0 --read-only --cap-drop ALL --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE \
      --security-opt no-new-privileges --pids-limit 128 --memory 768m --cpus 1 \
      --tmpfs /tmp:rw,nosuid,nodev,exec,size=128m,mode=1777 \
      --mount "type=bind,source=$case_dir/source,target=/src,readonly" \
      --mount "type=bind,source=$case_dir/evidence,target=/evidence" \
      --mount "type=volume,source=$socket_volume,target=/pgsocket" \
      -w /src -e PYTHONDONTWRITEBYTECODE=1 --entrypoint /usr/local/bin/python "$image" "$@"
}
docker volume create --label "ppt.p1.migration.owner=$run_id" "$socket_volume" >/dev/null
docker run --pull=never --rm --name "$helper_container" --label "ppt.p1.migration.owner=$run_id" \
  --network none --read-only --cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges \
  --mount "type=volume,source=$socket_volume,target=/pgsocket" \
  --entrypoint sh "$pg_image" -c 'chmod 1777 /pgsocket && chown 70:70 /pgsocket'
set +e
run_python unittest "$api_image" -m unittest discover -s ppt_service/deploy/k8s/migration/tests -p test_migration.py -v \
  > "$case_dir/evidence/unittest.log" 2>&1
unit_status=$?
set -e
if [ "$unit_status" != 0 ]; then cat "$case_dir/evidence/unittest.log"; exit "$unit_status"; fi
docker run --pull=never -d --name "$pg_container" --label "ppt.p1.migration.owner=$run_id" \
  --network none --user 70:70 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 512m --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev,size=384m,uid=70,gid=70,mode=700 \
  --mount "type=volume,source=$socket_volume,target=/pgsocket" \
  --mount "type=volume,source=$socket_volume,target=/var/run/postgresql" \
  --mount "type=bind,source=$case_dir/evidence,target=/evidence,readonly" \
  --mount "type=bind,source=$case_dir/source,target=/src,readonly" \
  -e POSTGRES_DB=ppt_p1_maintenance -e POSTGRES_HOST_AUTH_METHOD=trust -e PGHOST=/pgsocket \
  "$pg_image" -c listen_addresses= -c unix_socket_directories=/var/run/postgresql -c unix_socket_permissions=0777 >/dev/null
ready=0
for attempt in $(seq 1 30); do
  if docker exec "$pg_container" sh -c 'test "$(cat /proc/1/comm)" = postgres && pg_isready -h /pgsocket -U postgres -d ppt_p1_maintenance' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then docker logs "$pg_container" > "$case_dir/evidence/postgres.log" 2>&1; exit 2; fi
docker exec "$pg_container" psql -X -q -v ON_ERROR_STOP=1 -h /pgsocket -U postgres -d ppt_p1_maintenance \
  -v database=ppt_p1_source_fixture -v migrator=ppt_migrator -v runtime=ppt_runtime \
  -f /src/ppt_service/deploy/k8s/bootstrap-database.sql > "$case_dir/evidence/bootstrap.log" 2>&1
run_python seed "$site_image" /src/ppt_service/deploy/k8s/migration/tests/pg_fixture.py seed > "$case_dir/evidence/seed.json" 2> "$case_dir/evidence/seed.log"
umask 077
cat > "$case_dir/evidence/pg_service.conf" <<'SERVICE'
[ppt_migration_source]
host=/pgsocket
dbname=ppt_p1_source_fixture
user=ppt_migrator
[ppt_migration_target]
host=/pgsocket
dbname=ppt_p1_restore_fixture
user=ppt_migrator
SERVICE
: > "$case_dir/evidence/pgpass"
# The host-only wrapper transports psql stdin; application containers never
# receive the Docker socket or any ability to invoke this wrapper.
cat > "$case_dir/evidence/psql-wrapper" <<PYTHON
#!/usr/bin/python3
import os,subprocess,sys
arguments=sys.argv[1:]
index=arguments.index('--file')
script=arguments[index+1]
del arguments[index:index+2]
with open(script,'rb') as stream:
    result=subprocess.run(['docker','exec','-i','--user','0:0','-e','PGSERVICEFILE=/evidence/pg_service.conf','-e','PGPASSFILE=/evidence/pgpass','$pg_container','psql',*arguments],stdin=stream)
raise SystemExit(result.returncode)
PYTHON
chmod 700 "$case_dir/evidence/psql-wrapper"
inventory() {
    python3 "$tools/db_inventory.py" "$1" --schema public --service "$2" \
      --service-file "$case_dir/evidence/pg_service.conf" --passfile "$case_dir/evidence/pgpass" \
      --psql "$case_dir/evidence/psql-wrapper" "${@:3}"
}
inventory capture ppt_migration_source --output "$case_dir/evidence/database.json" > "$case_dir/evidence/capture.json"
db_hash=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest_sha256"])' "$case_dir/evidence/capture.json")
python3 "$tools/snapshot.py" inventory --root "$case_dir/evidence/source-data" --manifest "$case_dir/evidence/files.json" > "$case_dir/evidence/files-capture.json"
file_hash=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["manifest_sha256"])' "$case_dir/evidence/files-capture.json")
python3 "$tools/snapshot.py" copy --source "$case_dir/evidence/source-data" --destination "$case_dir/evidence/backup-data" \
  --manifest "$case_dir/evidence/files.json" --manifest-sha256 "$file_hash" --ownership preserve > "$case_dir/evidence/files-backup.json"
test ! -e "$case_dir/evidence/database.dump"
docker exec "$pg_container" pg_dump -h /pgsocket -U ppt_migrator -d ppt_p1_source_fixture \
  -Fc --schema=public --no-owner --no-privileges > "$case_dir/evidence/database.dump"
sha256sum "$case_dir/evidence/database.dump" > "$case_dir/evidence/dump.sha256"
inventory compare ppt_migration_source --expected "$case_dir/evidence/database.json" --expected-sha256 "$db_hash" > "$case_dir/evidence/source-after-dump.json"
cat > "$case_dir/evidence/target-bootstrap.sql" <<'SQL'
CREATE DATABASE ppt_p1_restore_fixture OWNER ppt_migrator;
REVOKE ALL ON DATABASE ppt_p1_restore_fixture FROM PUBLIC;
GRANT CONNECT ON DATABASE ppt_p1_restore_fixture TO ppt_runtime;
\connect ppt_p1_restore_fixture
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO ppt_migrator;
GRANT USAGE ON SCHEMA public TO ppt_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ppt_migrator IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO ppt_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ppt_migrator IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO ppt_runtime;
SQL
docker exec --user 0:0 "$pg_container" psql -X -q -v ON_ERROR_STOP=1 -h /pgsocket -U postgres -d ppt_p1_maintenance \
  -f /evidence/target-bootstrap.sql > "$case_dir/evidence/target-bootstrap.log" 2>&1
inventory check-empty ppt_migration_target > "$case_dir/evidence/target-empty.json"
sha256sum -c "$case_dir/evidence/dump.sha256" > "$case_dir/evidence/dump-check.log"
docker exec -i "$pg_container" pg_restore --list < "$case_dir/evidence/database.dump" > "$case_dir/evidence/archive.list"
python3 - "$case_dir/evidence/archive.list" "$case_dir/evidence/restore.list" <<'PYTHON'
import re,sys
from pathlib import Path
lines=Path(sys.argv[1]).read_text().splitlines(keepends=True)
pattern=re.compile(r'^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s+\S+\s*$')
matches=[i for i,line in enumerate(lines) if pattern.fullmatch(line.rstrip('\n'))]
if len(matches)!=1: raise RuntimeError('expected exactly one CREATE SCHEMA public archive entry')
lines[matches[0]]='; P1 existing verified-empty schema: '+lines[matches[0]]
with open(sys.argv[2],'x') as target: target.writelines(lines)
PYTHON
sha256sum "$case_dir/evidence/archive.list" "$case_dir/evidence/restore.list" > "$case_dir/evidence/restore-toc.sha256"
docker exec --user 0:0 -i "$pg_container" pg_restore -h /pgsocket -U ppt_migrator -d ppt_p1_restore_fixture \
  --use-list=/evidence/restore.list --exit-on-error --single-transaction --no-owner --no-privileges < "$case_dir/evidence/database.dump" \
  > "$case_dir/evidence/restore.log" 2>&1
inventory capture ppt_migration_target --output "$case_dir/evidence/restored-observed.json" > "$case_dir/evidence/restored-observed-capture.json"
inventory compare ppt_migration_target --expected "$case_dir/evidence/database.json" --expected-sha256 "$db_hash" \
  --output "$case_dir/evidence/restored-database.json" > "$case_dir/evidence/compare.json" || {
    diff -u "$case_dir/evidence/database.json" "$case_dir/evidence/restored-observed.json" > "$case_dir/evidence/restore-diff.log" || true
    exit 2
  }
python3 "$tools/snapshot.py" copy --source "$case_dir/evidence/backup-data" --destination "$case_dir/evidence/restored-data" \
  --manifest "$case_dir/evidence/files.json" --manifest-sha256 "$file_hash" --ownership preserve > "$case_dir/evidence/files-restored.json"
python3 "$tools/snapshot.py" verify --root "$case_dir/evidence/source-data" --manifest "$case_dir/evidence/files.json" \
  --manifest-sha256 "$file_hash" --ownership preserve > "$case_dir/evidence/files-old-unchanged.json"
run_python migrate "$site_image" /src/ppt_service/deploy/k8s/migration/tests/pg_fixture.py migrate-target > "$case_dir/evidence/migrate.json" 2> "$case_dir/evidence/migrate.log"
docker exec "$pg_container" psql -X -q -v ON_ERROR_STOP=1 -h /pgsocket -U ppt_migrator -d ppt_p1_restore_fixture \
  -v runtime=ppt_runtime -f /src/ppt_service/deploy/k8s/grant-runtime.sql > "$case_dir/evidence/grant.log" 2>&1
run_python runtime "$site_image" /src/ppt_service/deploy/k8s/migration/tests/pg_fixture.py runtime > "$case_dir/evidence/runtime.json" 2> "$case_dir/evidence/runtime.log"
inventory compare ppt_migration_source --expected "$case_dir/evidence/database.json" --expected-sha256 "$db_hash" > "$case_dir/evidence/source-final.json"
docker logs "$pg_container" > "$case_dir/evidence/postgres.log" 2>&1
printf '{"migration_unittest_exit":0,"bootstrap_exit":0,"pg_dump_restore_exit":0,"database_compare_exit":0,"files_compare_exit":0,"migrator_exit":0,"runtime_permission_exit":0,"source_unchanged":true,"model_requests":0,"fixture_only":true}\n' > "$case_dir/evidence/status.json"
cat "$case_dir/evidence/status.json"
