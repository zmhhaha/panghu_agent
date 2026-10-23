#!/usr/bin/env bash
set -euo pipefail
container=ppt-service-webdev-api-20261021
image=sha256:8cc3f72b86756b1644e850047b548fe91cef656f3b920bf5b72e7a62d1e40e93
base=/root/ppt-service-webdev-20261021
test "$(docker inspect --format '{{index .Config.Labels "app"}}' "$container")" = ppt-service-webdev
test "$(docker inspect --format '{{.State.Status}}' "$container")" = running
test "$(docker inspect --format '{{.Image}}' "$container")" = "$image"
test -f "$base/source/ppt_service/web/dist/index.html"
test -f "$base/source/ppt_service/web/dist/assets/index-DsAXq6Uo.js"
test -d "$base/application-data"
test -f "$base/api-db.env"
docker stop --time 20 "$container"
docker rm "$container"
docker run -d --name "$container" --label app=ppt-service-webdev \
 --network ppt-service-webdev-20261021 --user 10000:10000 \
 --env-file "$base/api-db.env" \
 --mount "type=bind,src=$base/application-data,dst=/work/development-data" \
 --mount "type=bind,src=$base/source/ppt_service/web/dist,dst=/opt/ppt-service/web-dist,readonly" \
 --read-only --cap-drop ALL --security-opt no-new-privileges \
 --tmpfs /tmp:rw,nosuid,nodev,size=128m --memory 768m \
 "$image"
docker inspect --format '{{.State.Status}} {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}} {{json .HostConfig.PortBindings}} {{.HostConfig.ReadonlyRootfs}}' "$container"
