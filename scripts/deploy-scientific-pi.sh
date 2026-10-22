#!/usr/bin/env bash
set -euo pipefail
# This script never builds/pushes shared latest images and never deletes data.
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
MODE=${1:---dry-run}
OUT=${2:-}
case "$MODE" in --dry-run|--render|--apply) ;; *) printf '%s\n' 'Usage: deploy-scientific-pi.sh [--dry-run|--render|--apply] [output-directory]' >&2; exit 64 ;; esac
: "${SCIENTIFIC_PI_WORKER_IMAGE:?set immutable worker repository@sha256:digest}"
: "${SCIENTIFIC_PI_API_IMAGE:?set separate compatible scientific API repository@sha256:digest}"
if [[ -z "$OUT" ]]; then OUT=$(mktemp -d "${TMPDIR:-/tmp}/scientific-pi-render.XXXXXX"); fi
mkdir -p -- "$OUT"
ARGS=(--namespace "${SCIENTIFIC_PI_NAMESPACE:-scientific-agent}" --worker-image "$SCIENTIFIC_PI_WORKER_IMAGE" --api-image "$SCIENTIFIC_PI_API_IMAGE" --model-endpoint "${SCIENTIFIC_PI_MODEL_BASE_URL:-http://llm-service.llm.svc.cluster.local/v1}" --model "${SCIENTIFIC_PI_MODEL:-llm-trusted}")
if [[ "${SCIENTIFIC_PI_ASSERT_VERIFIED:-0}" == 1 ]]; then ARGS+=(--assert-verified-runtime); fi
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --output "$OUT/worker.json"
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --api-patch --output "$OUT/api-patch.json"
python "$ROOT/k8s/scientific-pi/render.py" "${ARGS[@]}" --api-egress-policy --output "$OUT/api-egress-review.json"
printf 'Rendered reviewable files in %s\n' "$OUT"
if [[ "$MODE" != --apply ]]; then
  printf '%s\n' 'Local render only. No Kubernetes calls, credentials, build, push or deployment.'
  exit 0
fi
NAMESPACE=${SCIENTIFIC_PI_NAMESPACE:-scientific-agent}
# Namespace and ExternalSecret are managed separately via Vault inventory.
kubectl -n "$NAMESPACE" wait --for=condition=Ready externalsecret/scientific-pi-worker --timeout=60s
kubectl apply --dry-run=server -f "$OUT/worker.json"
kubectl apply -f "$OUT/worker.json"
if [[ "${SCIENTIFIC_PI_APPLY_API:-0}" == 1 ]]; then
  kubectl -n "$NAMESPACE" patch deployment/api --type strategic --patch-file "$OUT/api-patch.json"
fi
# API egress policy is intentionally NOT automatically applied. A new egress
# policy can isolate an API which previously had none; review union coverage.
printf '%s\n' 'Review API egress policy against existing policies before applying it.'
if [[ "${SCIENTIFIC_PI_ASSERT_VERIFIED:-0}" == 1 ]]; then
  kubectl -n "$NAMESPACE" rollout status deployment/scientific-pi-worker --timeout=120s
else
  printf '%s\n' 'Readiness intentionally remains false until real runtime/model/isolation acceptance is recorded.'
fi
