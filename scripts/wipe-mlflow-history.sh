#!/usr/bin/env bash
# Soft-delete all MLflow runs / experiments / registered models, then run
# ``mlflow gc`` to permanently purge soft-deleted runs and free artifact
# storage on the MLflow backing store.
#
# Sudo-free; uses the cluster-internal MLflow service via curl + jq, and
# kubectl exec into the mlflow pod for the gc step. The operator must
# have:
#   - kubectl access to the lolday namespace
#   - jq + curl in $PATH (install via scripts/install-tools.sh if absent)
#
# The script auto-sets up a kubectl port-forward to the mlflow svc on a
# free local port and tears it down on exit, so the operator does not
# need to manage the tunnel separately.
#
# Usage:
#   bash scripts/wipe-mlflow-history.sh
#
# Override:
#   NAMESPACE=lolday MLFLOW_API=http://localhost:5001/api/2.0/mlflow \
#     bash scripts/wipe-mlflow-history.sh
#
# This is a Phase 4 cutover step (see docs/superpowers/plans/
# 2026-05-02-maldet-2-and-runs-cleanup.md §4.6). DO NOT run outside that
# window.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
MLFLOW_POD="${MLFLOW_POD:-$(kubectl -n "$NAMESPACE" get pod \
  -l app.kubernetes.io/component=mlflow -o jsonpath='{.items[0].metadata.name}')}"

if [ -z "$MLFLOW_POD" ]; then
  echo "ERROR: could not find mlflow pod in namespace '$NAMESPACE'" >&2
  echo "       expected label app.kubernetes.io/component=mlflow" >&2
  exit 1
fi

# Set up port-forward to the in-cluster mlflow svc so the curl loop below
# can reach the REST API from the operator host. Pick a high port to
# minimise collisions; clean up on EXIT (success, error, or Ctrl-C).
PF_PORT="${PF_PORT:-15000}"
if [ -z "${MLFLOW_API:-}" ]; then
  kubectl -n "$NAMESPACE" port-forward "svc/mlflow" "${PF_PORT}:5000" \
    >/tmp/wipe-mlflow-pf.log 2>&1 &
  PF_PID=$!
  trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
  # Wait for the tunnel to accept connections (max ~10s).
  for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null \
      "http://localhost:${PF_PORT}/api/2.0/mlflow/experiments/search?max_results=1"
    then
      break
    fi
    sleep 0.5
  done
  MLFLOW_API="http://localhost:${PF_PORT}/api/2.0/mlflow"
fi

# Helpers — keep curl invocations DRY and ensure ``.runs``/``.experiments``
# null-safe via ``// []`` so an experiment with zero runs (or a fresh
# install) doesn't crash the iteration.
api_get() { curl -fsS "$MLFLOW_API$1"; }
api_post() {
  curl -fsS "$MLFLOW_API$1" -X POST -H 'Content-Type: application/json' -d "$2"
}
api_delete() {
  curl -fsS "$MLFLOW_API$1" -X DELETE -H 'Content-Type: application/json' -d "$2"
}

EXPERIMENTS_JSON=$(api_get "/experiments/search?max_results=1000")
NUM_EXP=$(echo "$EXPERIMENTS_JSON" | jq '(.experiments // []) | length')
NUM_RUNS=0
for exp in $(echo "$EXPERIMENTS_JSON" | jq -r '(.experiments // []) | .[].experiment_id'); do
  runs_json=$(api_post "/runs/search" \
    "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000,\"run_view_type\":\"ALL\"}")
  count=$(echo "$runs_json" | jq '(.runs // []) | length')
  NUM_RUNS=$((NUM_RUNS + count))
done
NUM_MODELS=$(api_get "/registered-models/search?max_results=1000" \
  | jq '(.registered_models // []) | length')

cat <<EOF

This will permanently delete:
  - $NUM_EXP experiments (excluding Default id=0)
  - $NUM_RUNS runs across all experiments (active + already soft-deleted)
  - $NUM_MODELS registered models with all versions
  + run mlflow gc to permanently purge soft-deleted runs and free
    artifact storage on the MLflow backing store.

EOF

read -r -p "Continue? Type 'yes' to proceed: " ans
if [ "$ans" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

echo "[1/4] Soft-deleting all runs..."
for exp in $(api_get "/experiments/search?max_results=1000" \
              | jq -r '(.experiments // []) | .[].experiment_id'); do
  for run in $(api_post "/runs/search" \
                "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000,\"run_view_type\":\"ACTIVE_ONLY\"}" \
                | jq -r '(.runs // []) | .[].info.run_id'); do
    api_post "/runs/delete" "{\"run_id\":\"$run\"}" > /dev/null
  done
done

# Registered-model and model-version deletes use the DELETE method (not
# POST — MLflow returns 405 for POST on these two endpoints, contrary to
# /runs/delete and /experiments/delete which are POST).
echo "[2/4] Deleting registered model versions and shells..."
for name in $(api_get "/registered-models/search?max_results=1000" \
              | jq -r '(.registered_models // []) | .[].name'); do
  for v in $(curl -fsS --get "$MLFLOW_API/model-versions/search" \
              --data-urlencode "filter=name='$name'" \
              --data-urlencode "max_results=1000" \
              | jq -r '(.model_versions // []) | .[].version'); do
    api_delete "/model-versions/delete" \
      "{\"name\":\"$name\",\"version\":\"$v\"}" > /dev/null
  done
  api_delete "/registered-models/delete" "{\"name\":\"$name\"}" > /dev/null
done

echo "[3/4] Soft-deleting experiments (skipping Default id=0)..."
for exp in $(api_get "/experiments/search?max_results=1000" \
              | jq -r '(.experiments // []) | .[].experiment_id'); do
  [ "$exp" = "0" ] && continue
  api_post "/experiments/delete" "{\"experiment_id\":\"$exp\"}" > /dev/null
done

# ``mlflow gc`` resolves the ``mlflow-artifacts:`` URI scheme via the
# tracking server, so we point MLFLOW_TRACKING_URI at the in-pod
# server (localhost:5000). --artifacts-destination is required because
# the chart writes artefacts to /mlflow-artifacts (not the package
# default ./mlruns, which is on the read-only root FS).
# The MLFLOW_BACKEND_STORE_URI env is wired into the chart (see
# templates/mlflow.yaml) so gc can read it via printenv without
# duplicating the URI shape here.
echo "[4/4] Running mlflow gc inside pod $MLFLOW_POD ..."
kubectl -n "$NAMESPACE" exec "$MLFLOW_POD" -- sh -c '
  exec env MLFLOW_TRACKING_URI=http://localhost:5000 \
    mlflow gc \
      --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
      --artifacts-destination /mlflow-artifacts
'

echo "Wipe complete."
