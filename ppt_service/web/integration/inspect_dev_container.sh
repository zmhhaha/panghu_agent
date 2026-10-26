#!/usr/bin/env bash
set -euo pipefail
docker inspect --format '{{.State.Status}} {{json .Config.Labels}} {{.Image}} {{.Config.User}} {{json .Config.Entrypoint}} {{json .Config.Cmd}} {{json .HostConfig.Binds}} {{json .HostConfig.Tmpfs}} {{json .HostConfig.SecurityOpt}} {{json .HostConfig.CapDrop}} {{.HostConfig.Memory}}' ppt-service-webdev-api-20261021
