#!/usr/bin/env bash
# Build only. Never starts a Gateway, model task, deployment or registry push by default.
set -euo pipefail

readonly PPT_MASTER_COMMIT=c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
base_image=""
image=""
publish=false

usage() {
  printf '%s\n' 'Usage: bash build_worker.sh --base-image REPOSITORY@sha256:DIGEST --image REPOSITORY:EXPLICIT_TAG [--push]' \
    'Requires a local .vendors/ppt-master.tar made by git archive at the fixed commit.' \
    'Default output is the local Docker image store; --push is explicit.'
}
while (($#)); do
  case "$1" in
    --base-image) base_image="${2:?missing base digest}"; shift 2 ;;
    --image) image="${2:?missing output image tag}"; shift 2 ;;
    --push) publish=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [[ ! "$base_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]; then
  printf 'An explicit base repository@sha256:<64 lowercase hex> is required.\n' >&2
  exit 2
fi
if [[ ! "$image" =~ ^[^[:space:]@]+:[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]] || [[ "${image##*:}" == latest ]]; then
  printf 'An explicit non-latest output image tag is required.\n' >&2
  exit 2
fi
archive="$SCRIPT_DIR/.vendors/ppt-master.tar"
[[ -f "$archive" && ! -L "$archive" ]] || { printf 'Missing regular vendor archive: %s\n' "$archive" >&2; exit 2; }
actual_commit="$(git get-tar-commit-id < "$archive")"
[[ "$actual_commit" == "$PPT_MASTER_COMMIT" ]] || { printf 'Vendor archive commit mismatch.\n' >&2; exit 2; }
archive_hash="$(sha256sum "$archive")"
archive_hash="${archive_hash%% *}"
printf 'Building linux/arm64 image %s from %s; PPT Master %s (%s)\n' "$image" "$base_image" "$actual_commit" "$archive_hash"
# Explicit tar context also protects builders without Dockerfile-specific ignore support.
# No credentials, uploads, checkpoints, tests, repository .git or other service files enter it.
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.env.*' \
  -C "$SCRIPT_DIR" -cf - __init__.py worker \
  images/Dockerfile.worker images/Dockerfile.worker.dockerignore \
  images/requirements-worker.lock images/check_runtime.py .vendors/ppt-master.tar \
  | docker build --platform linux/arm64 \
      --file images/Dockerfile.worker \
      --build-arg "HERMES_BASE_IMAGE=$base_image" \
      --build-arg "PPT_MASTER_COMMIT=$PPT_MASTER_COMMIT" \
      --build-arg "PPT_MASTER_ARCHIVE_SHA256=$archive_hash" \
      --tag "$image" -
if "$publish"; then docker push "$image"; fi
