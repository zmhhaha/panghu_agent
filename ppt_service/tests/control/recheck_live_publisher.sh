#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022
base=/root/ppt-service-webdev-20261021/source/ppt_service
source=/root/ppt-service-sample/acceptance-v3
ssh "$remote" "mkdir -p $case/tests"
scp ppt_service/control/{publisher,dispatcher}.py "$remote:$case/code/control/"
scp ppt_service/tests/control/{test_control.py,test_publisher_gate.py,verify_publisher_gate.py} "$remote:$case/tests/"
scp ppt_service/tests/control/live_publisher_fixture.py "$remote:$case/scripts/"
ssh "$remote" "chmod -R a+rX $case/code $case/tests $case/scripts"
ssh "$remote" "docker run --rm --name ppt-v1-gate-regression --user 0 --network ppt-service-webdev-20261021 --env-file /root/ppt-service-webdev-20261021/api-db.env --entrypoint python -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $base/worker:/opt/ppt-service/ppt_service/worker:ro -v $case/tests:/tests:ro ppt-service-api:parse-dev-20261022 /tests/verify_publisher_gate.py"
for action in run forged; do
  ssh "$remote" "docker run --rm --name ppt-v1-fixed-check --user 0 --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --network none --tmpfs /tmp:rw,nosuid,nodev,size=128m --entrypoint python -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock -v $case:$case -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $case/scripts/live_publisher_fixture.py:/ops/live_publisher_fixture.py:ro ppt-service-api:parse-dev-20261022 /ops/live_publisher_fixture.py $action --case $case"
done
ssh "$remote" "docker run --rm --name ppt-v1-source-after --user 0 --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --network none --entrypoint python -v $source:$source:ro -v $case:$case -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $case/scripts/live_publisher_fixture.py:/ops/live_publisher_fixture.py:ro ppt-service-api:parse-dev-20261022 /ops/live_publisher_fixture.py verify-source --case $case"
