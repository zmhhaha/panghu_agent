#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
scp ppt_service/server/*.py "$remote:$base/server/"
scp ppt_service/tests/server/test_*.py "$remote:$base/tests/server/"
ssh "$remote" "docker build --build-arg PYTHON_API_BASE=python:3.13-slim -t ppt-service-api:dev-20261021 -f $base/images/Dockerfile.api-dev $base"
