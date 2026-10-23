#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022
source=/root/ppt-service-sample/acceptance-v3
scp ppt_service/control/{render_validation,publisher,pods,planning}.py "$remote:$case/code/control/"
scp ppt_service/tests/control/live_render_check.py "$remote:$case/scripts/"
ssh "$remote" "chmod -R a+rX $case/code $case/scripts"
ssh "$remote" "docker run --rm --name ppt-render-live-controller --user 0 --read-only --cap-drop ALL --cap-add DAC_OVERRIDE --security-opt no-new-privileges --network none --tmpfs /tmp:rw,nosuid,nodev,size=128m --entrypoint python -v /usr/bin/docker:/usr/bin/docker:ro -v /var/run/docker.sock:/var/run/docker.sock -v $source:$source:ro -v $case:$case -v $case/code/control:/opt/ppt-service/ppt_service/control:ro -v $case/scripts/live_render_check.py:/ops/live_render_check.py:ro ppt-service-api:parse-dev-20261022 /ops/live_render_check.py"
