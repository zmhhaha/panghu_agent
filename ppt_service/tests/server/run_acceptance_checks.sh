#!/usr/bin/env bash
# Isolated P4 check; no live database, source, credentials or model calls.
set -euo pipefail
case_root=/root/ppt-service-p4-20261022
image=sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7
test -f "$case_root/source.tar"
test ! -e "$case_root/code"
mkdir "$case_root/code"
tar -xf "$case_root/source.tar" -C "$case_root/code"
chmod -R a+rX "$case_root/code"
docker run --rm --name ppt-p4-contract-20261022 --network none --read-only \
  --user 10000:10000 --workdir /code --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 --memory 512m --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  -e PYTHONPATH=/code -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$case_root/code:/code:ro" --entrypoint python "$image" \
  -m unittest discover -s /code/ppt_service/tests/server -p test_acceptance.py -v \
  2>&1 | tee "$case_root/unit-results.txt"
test -z "$(docker ps -aq --filter name=^/ppt-p4-contract-20261022$)"
