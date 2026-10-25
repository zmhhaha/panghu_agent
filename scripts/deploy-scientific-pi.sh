#!/usr/bin/env bash
set -euo pipefail
# This script never builds/pushes shared latest images and never deletes data.
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:---dry-run}
OUT=${2:-}
case "$MODE" in --dry-run|--render|--apply|--apply-api) ;; *) printf '%s\n' 'Usage: deploy-scientific-pi.sh [--dry-run|--render|--apply|--apply-api] [output-directory]' >&2; exit 64 ;; esac
: "${SCIENTIFIC_PI_WORKER_IMAGE:?set immutable worker repository@sha256:digest}"
: "${SCIENTIFIC_PI_API_IMAGE:?set dedicated Pi-only scientific API repository@sha256:digest}"
if [[ -z "$OUT" ]]; then OUT=$(mktemp -d "${TMPDIR:-/tmp}/scientific-pi-render.XXXXXX"); fi
mkdir -p -- "$OUT"
ARGS=(--namespace "${SCIENTIFIC_PI_NAMESPACE:-scientific-agent}" --worker-image "$SCIENTIFIC_PI_WORKER_IMAGE" --api-image "$SCIENTIFIC_PI_API_IMAGE" --model-endpoint "${SCIENTIFIC_PI_MODEL_BASE_URL:-http://llm-service.llm.svc.cluster.local/v1}" --model "${SCIENTIFIC_PI_MODEL:-llm-trusted}")
if [[ "${SCIENTIFIC_PI_ASSERT_VERIFIED:-0}" == 1 ]]; then ARGS+=(--assert-verified-runtime); fi
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --output "$OUT/worker.json"
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --api-patch --output "$OUT/api-patch.json"
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --api-egress-policy --output "$OUT/api-egress-review.json"
printf 'Rendered reviewable files in %s\n' "$OUT"
if [[ "$MODE" != --apply && "$MODE" != --apply-api ]]; then
  printf '%s\n' 'Local render only. No Kubernetes calls, credentials, build, push or deployment.'
  exit 0
fi
NAMESPACE=${SCIENTIFIC_PI_NAMESPACE:-scientific-agent}
# Namespace and ExternalSecret are managed separately via Vault inventory.
kubectl -n "$NAMESPACE" wait --for=condition=Ready externalsecret/scientific-pi-worker --timeout=60s
if [[ "$MODE" == --apply ]]; then
  kubectl apply --dry-run=server -f "$OUT/worker.json"
  kubectl apply -f "$OUT/worker.json"
fi
if [[ "$MODE" == --apply-api || "${SCIENTIFIC_PI_APPLY_API:-0}" == 1 ]]; then
  # Stop legacy submissions at the entrypoint before invoking this command.
  # An API restart must not terminate an in-flight CrewAI thread.
  LEGACY_ACTIVE=$(kubectl -n "$NAMESPACE" exec deployment/api -c api -- python -c "from tools import sqlite_client as db; print(db._query(\"SELECT COUNT(*) AS count FROM scientific_tasks WHERE status IN ('pending','running')\")[0]['count'])")
  if [[ "$LEGACY_ACTIVE" != 0 ]]; then
    printf '%s\n' 'API rollout refused: active legacy research tasks exist or drain could not be confirmed.' >&2
    exit 1
  fi
  # Save the complete old spec before removing overlays/credentials. The old
  # immutable ConfigMaps and worker PVC remain available for rollback.
  if [[ -e "$OUT/api-before.json" || -e "$OUT/api-rollback.json" ]]; then
    printf '%s\n' 'API rollout refused: rollback evidence already exists; use a fresh output directory.' >&2
    exit 1
  fi
  umask 077
  kubectl -n "$NAMESPACE" get deployment/api -o json > "$OUT/api-before.json"
  python - "$OUT/api-before.json" "$OUT/api-rollback.json" <<'PY'
import json
from pathlib import Path
import sys
saved = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps([{'op': 'replace', 'path': '/spec', 'value': saved['spec']}], indent=2) + '\n')
PY
  kubectl -n "$NAMESPACE" patch deployment/api --type strategic --patch-file "$OUT/api-patch.json" --dry-run=server
  kubectl -n "$NAMESPACE" patch deployment/api --type strategic --patch-file "$OUT/api-patch.json"
  kubectl -n "$NAMESPACE" rollout status deployment/api --timeout=180s
  printf 'API rollback patch saved in %s/api-rollback.json\n' "$OUT"
fi
# API egress policy is intentionally NOT automatically applied. A new egress
# policy can isolate an API which previously had none; review union coverage.
printf '%s\n' 'Review API egress policy against existing policies before applying it.'
if [[ "$MODE" == --apply-api ]]; then
  printf '%s\n' 'Existing worker image, acceptance flags, credentials, network policy and PVC were left in place.'
elif [[ "${SCIENTIFIC_PI_ASSERT_VERIFIED:-0}" == 1 ]]; then
  kubectl -n "$NAMESPACE" rollout status deployment/scientific-pi-worker --timeout=120s
else
  printf '%s\n' 'Readiness intentionally remains false until real runtime/model/isolation acceptance is recorded.'
fi
