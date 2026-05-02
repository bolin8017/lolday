#!/usr/bin/env bash
# Soft-delete all MLflow runs / experiments / registered models, then run
# `mlflow gc` to permanently purge soft-deleted runs and free artifact
# storage.
#
# Sudo-free; uses the cluster-internal MLflow service via curl + jq, and
# kubectl exec into the mlflow-server pod for the gc step. The operator
# must have:
#   - kubectl access to the lolday namespace
#   - jq + curl in $PATH (install via scripts/install-tools.sh if absent)
#
# Usage:
#   bash scripts/wipe-mlflow-history.sh
#
# Override:
#   NAMESPACE=lolday MLFLOW_API=http://mlflow-server.lolday.svc/api/2.0/mlflow \
#     bash scripts/wipe-mlflow-history.sh
#
# This is a Phase 4 cutover step (see docs/superpowers/plans/
# 2026-05-02-maldet-2-and-runs-cleanup.md §4.6). DO NOT run outside that
# window.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
MLFLOW_POD="${MLFLOW_POD:-$(kubectl -n "$NAMESPACE" get pod \
  -l app=mlflow-server -o jsonpath='{.items[0].metadata.name}')}"
MLFLOW_API="${MLFLOW_API:-http://mlflow-server.${NAMESPACE}.svc/api/2.0/mlflow}"

if [ -z "$MLFLOW_POD" ]; then
  echo "ERROR: could not find mlflow-server pod in namespace '$NAMESPACE'" >&2
  exit 1
fi

echo "Counting current resources via $MLFLOW_API ..."
NUM_EXP=$(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" \
  | jq '.experiments | length')
NUM_RUNS=0
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" \
              | jq -r '.experiments[].experiment_id'); do
  RUN_COUNT=$(curl -fsS "$MLFLOW_API/runs/search" -X POST \
              -H 'Content-Type: application/json' \
              -d "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000}" \
              | jq '.runs | length')
  NUM_RUNS=$((NUM_RUNS + RUN_COUNT))
done
NUM_MODELS=$(curl -fsS \
  "$MLFLOW_API/registered-models/search?max_results=1000" \
  | jq '.registered_models | length')

cat <<EOF

This will permanently delete:
  - $NUM_EXP experiments (excluding Default id=0)
  - $NUM_RUNS runs across all experiments
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
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" \
              | jq -r '.experiments[].experiment_id'); do
  for run in $(curl -fsS "$MLFLOW_API/runs/search" -X POST \
                -H 'Content-Type: application/json' \
                -d "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000}" \
                | jq -r '.runs[].info.run_id'); do
    curl -fsS "$MLFLOW_API/runs/delete" -X POST \
      -H 'Content-Type: application/json' \
      -d "{\"run_id\":\"$run\"}" > /dev/null
  done
done

echo "[2/4] Deleting registered model versions and shells..."
for name in $(curl -fsS \
              "$MLFLOW_API/registered-models/search?max_results=1000" \
              | jq -r '.registered_models[].name'); do
  for v in $(curl -fsS --get "$MLFLOW_API/model-versions/search" \
              --data-urlencode "filter=name='$name'" \
              --data-urlencode "max_results=1000" \
              | jq -r '.model_versions[].version'); do
    curl -fsS "$MLFLOW_API/model-versions/delete" -X POST \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"$name\",\"version\":\"$v\"}" > /dev/null
  done
  curl -fsS "$MLFLOW_API/registered-models/delete" -X POST \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"$name\"}" > /dev/null
done

echo "[3/4] Soft-deleting experiments (skipping Default id=0)..."
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" \
              | jq -r '.experiments[].experiment_id'); do
  [ "$exp" = "0" ] && continue
  curl -fsS "$MLFLOW_API/experiments/delete" -X POST \
    -H 'Content-Type: application/json' \
    -d "{\"experiment_id\":\"$exp\"}" > /dev/null
done

echo "[4/4] Running mlflow gc inside pod $MLFLOW_POD ..."
BACKEND_URI=$(kubectl -n "$NAMESPACE" exec "$MLFLOW_POD" -- \
  printenv MLFLOW_BACKEND_STORE_URI)
kubectl -n "$NAMESPACE" exec "$MLFLOW_POD" -- \
  mlflow gc --backend-store-uri "$BACKEND_URI"

echo "Wipe complete."
