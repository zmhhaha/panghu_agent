#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
ssh "$remote" "mkdir -p $base/tests/control $base/worker"
scp ppt_service/control/{execution,staging,parsing,pods,publisher,dispatcher}.py "$remote:$base/control/"
scp ppt_service/worker/*.py "$remote:$base/worker/"
scp ppt_service/tests/control/*.py "$remote:$base/tests/control/"
ssh "$remote" "docker run --rm --name ppt-control-tests --user 0 --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -v $base/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $base/tests/control:/tests/control:ro ppt-service-api:dev-20261021 /tests/control/verify.py"
