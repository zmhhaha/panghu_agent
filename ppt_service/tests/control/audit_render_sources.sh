#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022/render-check
ssh "$remote" bash -s <<'REMOTE'
set -euo pipefail
case=/root/ppt-service-webdev-20261021/publisher-live-v1-20261022/render-check
mkdir -p "$case/official"
cid=$(docker create --name ppt-render-source-audit-20261022 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --entrypoint python sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a --version)
echo "source_audit_container=$cid"
trap 'docker rm "$cid" >/dev/null' EXIT
for script in visual_review.py pptx_delivery_check.py svg_to_pptx.py; do
  docker cp "$cid:/opt/ppt-master/skills/ppt-master/scripts/$script" "$case/official/$script"
done
REMOTE
scp "$remote:$case/official/visual_review.py" ppt_service/tests/control/.runtime/official/visual_review.py
scp "$remote:$case/official/pptx_delivery_check.py" ppt_service/tests/control/.runtime/official/pptx_delivery_check.py
scp "$remote:$case/official/svg_to_pptx.py" ppt_service/tests/control/.runtime/official/svg_to_pptx.py
