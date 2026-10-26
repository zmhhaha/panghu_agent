#!/usr/bin/env bash
set -euo pipefail
remote=root@192.168.137.101
case=/root/ppt-service-network-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM
ssh "$remote" "mkdir $case && mkdir -p $case/code/ppt_service/control $case/scripts"
scp ppt_service/__init__.py "$remote:$case/code/ppt_service/"
scp ppt_service/control/{__init__,execution,staging,pods}.py "$remote:$case/code/ppt_service/control/"
scp ppt_service/tests/control/test_network_isolation{_preflight,_fixture,_live,_rules,_volume_cleanup}.py "$remote:$case/scripts/"
ssh "$remote" "PYTHONPATH=$case/code:$case/scripts python3 -m unittest test_network_isolation_rules test_network_isolation_volume_cleanup -v"
ssh "$remote" "PYTHONPATH=$case/code python3 $case/scripts/test_network_isolation_preflight.py --case $case"
ssh "$remote" "PYTHONPATH=$case/code python3 $case/scripts/test_network_isolation_live.py --case $case"
