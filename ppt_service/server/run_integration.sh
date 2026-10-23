#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
scp ppt_service/server/*.py "$remote:$base/server/"
scp ppt_service/tests/server/test_*.py "$remote:$base/tests/server/"
ssh "$remote" "docker run --rm --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -v $base/server:/opt/ppt-service/ppt_service/server:ro -v $base/tests/server:/opt/ppt-service/tests/server:ro ppt-service-api:dev-20261021 -m ppt_service.server.verify_container"
