#!/usr/bin/env bash
set -euo pipefail
ssh root@192.168.137.101 bash -s <<'REMOTE'
set -euo pipefail
home=/root/ppt-service-webdev-20261021
backup=$home/backups/pre-materials-20261022
docker run --rm --name ppt-materials-final-counts --user 0 --network ppt-service-webdev-20261021 --env-file "$home/api-db.env" --entrypoint python -v "$backup:/backup" -v "$home/source/ppt_service/tests/control/deploy_materials.py:/ops/deploy_materials.py:ro" ppt-service-api:parse-dev-20261022 /ops/deploy_materials.py status
docker ps --filter label=app=ppt-service-materials-dev --format '{{.ID}} {{.Names}} {{.Status}}'
docker ps -a --filter label=io.ppt-service.control --format '{{.ID}} {{.Names}} {{.Status}}'
docker logs --tail 5 ppt-service-materials-dev-20261022
REMOTE
