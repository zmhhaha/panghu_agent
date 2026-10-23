#!/usr/bin/env bash
set -euo pipefail
ssh root@192.168.137.101 bash -s <<'REMOTE'
set -euo pipefail
python3 - <<'PY'
import json
from pathlib import Path
root=Path('/root/ppt-service-webdev-20261021/publisher-live-v1-20261022/validation-evidence')
for nonce in ('0c5b6c3f-7f0c-4f41-b0fa-c7a3ef53be62','0f404cde-aef5-42ef-bc8f-42b6741795b6'):
    data=json.loads((root/nonce/'transport-manifest.json').read_text())
    print(json.dumps({k:data.get(k) for k in ('controller_nonce','container_id','sandbox_exit_code','writers_stopped','transport_verified','controller_error_code','bytes')}))
PY
docker ps -a --filter name=ppt-render- --format '{{.Names}}'
docker ps -a --filter name=ppt-validator- --format '{{.Names}}'
REMOTE
