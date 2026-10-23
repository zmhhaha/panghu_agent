#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
root=/root/ppt-service-webdev-20261021/control-sandbox-data
scp ppt_service/control/{execution,staging,parsing,pods,publisher,dispatcher}.py "$remote:$base/control/"
scp ppt_service/tests/control/test_docker_sandbox.py "$remote:$base/tests/control/"
ssh "$remote" "mkdir -p $root"
ssh "$remote" "docker run --rm --name ppt-control-sandbox-tests --user 0 --network none --entrypoint python -e PPT_RUN_DOCKER_SANDBOX_TESTS=true -e PPT_CONTROL_HOST_DATA_ROOT=$root -e PPT_CONTROL_HOST_CODE_ROOT=$base/control -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock -v $root:$root -v $base:$base:ro -v $base/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $base/tests/control:/tests/control:ro ppt-service-api:dev-20261021 -m pytest /tests/control/test_docker_sandbox.py -q --tb=short -p no:cacheprovider"
