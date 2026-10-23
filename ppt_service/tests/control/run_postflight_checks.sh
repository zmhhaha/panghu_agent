#!/usr/bin/env bash
# Uploaded isolated package; no live service files, DB, or model credentials.
set -euo pipefail
case_root=${PPT_P3_CASE_ROOT:-/root/ppt-service-p3-20261022}
snapshot=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022/snapshot
request_root=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022
mkdir -p "$case_root/code"
tar -xf "$case_root/source.tar" -C "$case_root/code"
chmod -R a+rX "$case_root/code"
docker run --rm --name ppt-p3-unit-20261022 --network none --read-only --user 10000:10000 --workdir /code \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 512m --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  -e PYTHONPATH=/code -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$case_root/code:/code:ro" --entrypoint python \
  ppt-service-api:parse-dev-20261022 -m pytest \
  /code/ppt_service/tests/control/test_postflight_validation.py -q --tb=short -p no:cacheprovider \
  | tee "$case_root/unit-results.txt"

output="$case_root/official-v1"
test ! -e "$output"
install -d -o 10000 -g 10000 -m 0700 "$output"
docker run --rm --name ppt-p3-official-20261022 --network none --read-only --user 10000:10000 --workdir /code \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 --memory 1g --cpus 2 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 --tmpfs /opt/data:rw,nosuid,nodev,size=1m \
  -e PYTHONPATH=/code -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$case_root/code:/code:ro" -v "$snapshot:/snapshot:ro" \
  -v "$request_root/request.json:/request/request.json:ro" -v "$output:/output:rw" \
  --entrypoint /opt/hermes/.venv/bin/python \
  sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a \
  /code/ppt_service/tests/control/live_postflight_check.py | tee "$case_root/official-results.txt"
