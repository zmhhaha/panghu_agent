#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@192.168.137.101 "docker run --rm --network none --entrypoint python ppt-service-worker:arm64-c350bc31-preview5 -c 'import fastapi,sqlalchemy,jwt;print(fastapi.__version__,sqlalchemy.__version__)'"
