#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes root@192.168.137.101 bash -s <<'REMOTE'
set -euo pipefail
docker exec ppt-service-webdev-db-20261021 sh -c 'createdb -U "$POSTGRES_USER" ppt_service_integration'
REMOTE
