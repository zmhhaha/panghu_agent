#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
staged=/root/ppt-service-webdev-20261021/materials-test-source
root=/root/ppt-service-webdev-20261021/material-integration-data
ssh "$remote" "mkdir -p $staged $root"
scp ppt_service/server/{models,migrate,app}.py "$remote:$staged/"
scp ppt_service/control/materials.py "$remote:$base/control/"
scp ppt_service/tests/control/test_materials.py "$remote:$base/tests/control/"
ssh "$remote" "docker run --rm --name ppt-material-integration-tests --user 0 --cap-drop ALL --security-opt no-new-privileges --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -e PPT_RUN_MATERIAL_DOCKER=true -e PPT_ALLOW_PAID_EXECUTION=false -e PPT_CONTROL_HOST_DATA_ROOT=$root -e PPT_CONTROL_HOST_CODE_ROOT=$base/control -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock -v $root:$root -v $base:$base:ro -v $staged/models.py:/opt/ppt-service/ppt_service/server/models.py:ro -v $staged/migrate.py:/opt/ppt-service/ppt_service/server/migrate.py:ro -v $staged/app.py:/opt/ppt-service/ppt_service/server/app.py:ro -v $base/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $base/tests/control:/tests/control:ro ppt-service-api:dev-20261021 /tests/control/test_materials.py"
