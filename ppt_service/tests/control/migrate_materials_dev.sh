#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
scp ppt_service/tests/control/deploy_materials.py "$remote:$base/tests/control/"
ssh "$remote" bash -s <<'REMOTE'
set -euo pipefail
umask 077
home=/root/ppt-service-webdev-20261021
backup=$home/backups/pre-materials-20261022
mkdir -p "$backup"
test ! -e "$backup/database.sql"
docker exec ppt-service-webdev-db-20261021 sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$backup/database.sql"
test -s "$backup/database.sql"
docker inspect --format '{{.Image}}' ppt-service-webdev-api-20261021 > "$backup/old-api-image.txt"
docker inspect --format '{{json .Mounts}}' ppt-service-webdev-api-20261021 > "$backup/old-api-mounts.json"
docker run --rm --name ppt-materials-before --user 0 --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -v "$backup:/backup" -v "$home/source/ppt_service/tests/control/deploy_materials.py:/ops/deploy_materials.py:ro" ppt-service-api:parse-dev-20261022 /ops/deploy_materials.py snapshot
docker run --rm --name ppt-materials-migrate --user 0 --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -v "$backup:/backup" -v "$home/source/ppt_service/tests/control/deploy_materials.py:/ops/deploy_materials.py:ro" ppt-service-api:parse-dev-20261022 /ops/deploy_materials.py migrate
test "$(docker inspect --format '{{index .Config.Labels "app"}}' ppt-service-webdev-api-20261021)" = ppt-service-webdev
docker stop --time 20 ppt-service-webdev-api-20261021
docker rm ppt-service-webdev-api-20261021
docker run -d --name ppt-service-webdev-api-20261021 --label app=ppt-service-webdev --label io.ppt-service.component=api --network ppt-service-webdev-20261021 --ip 172.18.0.3 --restart unless-stopped --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=128m --env-file "$home/api-db.env" --mount "type=bind,src=$home/application-data,dst=/work/development-data" --mount "type=bind,src=$home/source/ppt_service/web/dist,dst=/opt/ppt-service/web-dist,readonly" ppt-service-api:parse-dev-20261022
docker inspect --format '{{.Id}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ppt-service-webdev-api-20261021
REMOTE
