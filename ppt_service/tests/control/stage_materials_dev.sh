#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
base=/root/ppt-service-webdev-20261021/source/ppt_service
scp ppt_service/server/{models,migrate,app}.py "$remote:$base/server/"
scp ppt_service/control/{materials.py,README.md} "$remote:$base/control/"
scp -r ppt_service/web/dist/. "$remote:$base/web/dist/"
ssh "$remote" bash -s <<'REMOTE'
set -euo pipefail
docker inspect --format '{{json .Mounts}}' ppt-service-webdev-api-20261021
docker inspect --format '{{json .NetworkSettings.Networks}}' ppt-service-webdev-api-20261021
docker inspect --format '{{json .HostConfig.PortBindings}}' ppt-service-webdev-api-20261021
docker build --build-arg PYTHON_API_BASE=python:3.13-slim -t ppt-service-api:parse-dev-20261022 -f /root/ppt-service-webdev-20261021/source/ppt_service/images/Dockerfile.api-dev /root/ppt-service-webdev-20261021/source/ppt_service
REMOTE
