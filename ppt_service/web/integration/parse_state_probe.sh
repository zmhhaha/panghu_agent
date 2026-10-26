#!/usr/bin/env bash
set -euo pipefail
docker inspect --format '{{.Name}} status={{.State.Status}} restart_count={{.RestartCount}} oom={{.State.OOMKilled}} started={{.State.StartedAt}} error={{json .State.Error}}' ppt-service-webdev-api-20261021 ppt-service-materials-dev-20261022
