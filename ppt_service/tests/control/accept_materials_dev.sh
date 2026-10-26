#!/usr/bin/env bash
set -euo pipefail
ssh root@192.168.137.101 bash -s <<'REMOTE'
set -euo pipefail
home=/root/ppt-service-webdev-20261021
backup=$home/backups/pre-materials-20261022
image=ppt-service-api:parse-dev-20261022
helper=$home/source/ppt_service/tests/control/deploy_materials.py
if test ! -e "$backup/acceptance.json"; then
  docker run --rm --name ppt-materials-http-create --user 0 --network ppt-service-webdev-20261021 --entrypoint python -v "$backup:/backup" -v "$helper:/ops/deploy_materials.py:ro" "$image" /ops/deploy_materials.py create
fi
project=$(docker run --rm --user 0 --network none --entrypoint python -v "$backup:/backup:ro" "$image" -c 'import json;print(json.load(open("/backup/acceptance.json"))["project_id"])')
for step in 1 2; do
docker run --rm --name ppt-materials-acceptance-once --user 0 --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=128m --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -e DATA_ROOT="$home/application-data" -e PPT_CONTROL_CODE_ROOT="$home/source/ppt_service/control" -e PPT_ALLOW_PAID_EXECUTION=false -e PPT_EXECUTION_ENABLED=false -e PPT_CONTROL_STATE=disabled -v "$home/application-data:$home/application-data" -v "$home/source/ppt_service/control:$home/source/ppt_service/control:ro" -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock "$image" -m ppt_service.control.materials --once --project-id "$project"
done
docker run --rm --name ppt-materials-http-check --user 0 --network ppt-service-webdev-20261021 --entrypoint python -v "$backup:/backup" -v "$helper:/ops/deploy_materials.py:ro" "$image" /ops/deploy_materials.py check
docker run -d --name ppt-service-materials-dev-20261022 --label app=ppt-service-materials-dev --label io.ppt-service.component=material-parser --restart unless-stopped --log-driver local --log-opt max-size=10m --log-opt max-file=2 --user 0 --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=128m --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -e DATA_ROOT="$home/application-data" -e PPT_CONTROL_CODE_ROOT="$home/source/ppt_service/control" -e PPT_ALLOW_PAID_EXECUTION=false -e PPT_EXECUTION_ENABLED=false -e PPT_CONTROL_STATE=disabled -v "$home/application-data:$home/application-data" -v "$home/source/ppt_service/control:$home/source/ppt_service/control:ro" -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock "$image" -m ppt_service.control.materials
docker inspect --format '{{.Id}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' ppt-service-materials-dev-20261022
docker run --rm --name ppt-materials-counts --user 0 --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -v "$backup:/backup" -v "$helper:/ops/deploy_materials.py:ro" "$image" /ops/deploy_materials.py status
REMOTE
