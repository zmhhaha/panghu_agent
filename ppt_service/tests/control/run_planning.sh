#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-webdev-20261021/planning-compatibility-20261022
base=/root/ppt-service-webdev-20261021/source/ppt_service
ssh "$remote" "mkdir -p $case/code/control $case/tests $case/fixtures $case/output"
scp ppt_service/control/{__init__,execution,staging,parsing,pods,publisher,dispatcher,planning}.py "$remote:$case/code/control/"
scp ppt_service/tests/control/{test_control.py,test_publisher_gate.py,test_planning.py,verify_planning.py} "$remote:$case/tests/"
scp ppt_service/.runtime/recovered-options/result/manifest.json "$remote:$case/fixtures/stage2-manifest.json"
scp ppt_service/.runtime/recovered-options/generate-snapshot/acceptance-public-sample_20261021/planning/stage1-proposal.json "$remote:$case/fixtures/stage1-proposal.json"
ssh "$remote" "chmod -R a+rX $case/code $case/tests $case/fixtures"
ssh "$remote" "docker run --rm --name ppt-planning-compatibility-tests --user 0 --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -e PLANNING_PROJECTION_OUTPUT=/projection-output -e PLANNING_STAGE2_FIXTURE=/fixtures/stage2-manifest.json -e PLANNING_STAGE1_FIXTURE=/fixtures/stage1-proposal.json -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $case/tests:/tests:ro -v $case/fixtures:/fixtures:ro -v $case/output:/projection-output ppt-service-api:parse-dev-20261022 /tests/verify_planning.py"
