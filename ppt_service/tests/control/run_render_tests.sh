#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022
base=/root/ppt-service-webdev-20261021/source/ppt_service
ssh "$remote" "mkdir -p $case/render-check/tests $case/render-check/parser-regression"
scp ppt_service/control/{render_validation,pods,publisher,planning,dispatcher}.py "$remote:$case/code/control/"
scp ppt_service/tests/control/{test_control.py,test_publisher_gate.py,test_render_validation.py,test_docker_sandbox.py,verify_render.py} "$remote:$case/render-check/tests/"
ssh "$remote" "chmod -R a+rX $case/code $case/render-check/tests"
ssh "$remote" "docker run --rm --name ppt-render-unit-tests --user 0 --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $case/render-check/tests:/tests:ro ppt-service-api:parse-dev-20261022 /tests/verify_render.py"
ssh "$remote" "docker run --rm --name ppt-render-parse-regression --user 0 --network none --entrypoint python -e PPT_RUN_DOCKER_SANDBOX_TESTS=true -e PPT_CONTROL_HOST_DATA_ROOT=$case/render-check/parser-regression -e PPT_CONTROL_HOST_CODE_ROOT=$case/code/control -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock -v $case:$case -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $case/render-check/tests:/tests:ro ppt-service-api:parse-dev-20261022 -m pytest /tests/test_docker_sandbox.py -q --tb=short -p no:cacheprovider"
