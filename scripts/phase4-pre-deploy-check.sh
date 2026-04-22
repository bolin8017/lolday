#!/usr/bin/env bash
set -euo pipefail

# Pre-flight checks for Phase 4 deploy.
# Confirms: sample dirs exist + readable, MLflow password set, Harbor is up.

SAMPLES_DIR=${SAMPLES_DIR:-/mnt/ssd500g/data/samples}

echo "[1/4] Checking sample directory..."
if [[ ! -d "$SAMPLES_DIR" ]]; then
  echo "WARN: $SAMPLES_DIR does not exist."
  echo "  -> sudo mkdir -p $SAMPLES_DIR && sudo chown $USER:$USER $SAMPLES_DIR && sudo chmod 755 $SAMPLES_DIR"
  echo "  Samples must be laid out flat: $SAMPLES_DIR/<sha256[:2]>/<sha256> (per upxelfdet convention)."
  echo "  (Phase 4 can deploy without samples, but jobs will fail integrity checks until populated.)"
else
  echo "  OK: $SAMPLES_DIR"
fi

echo "[2/4] Checking MLflow DB password is set..."
if [[ -z "${MLFLOW_DB_PASSWORD:-}" ]]; then
  echo "FAIL: MLFLOW_DB_PASSWORD env var not set. Generate one:"
  echo "  export MLFLOW_DB_PASSWORD=\$(openssl rand -base64 32 | tr -d '=+/')"
  exit 1
fi
echo "  OK: MLFLOW_DB_PASSWORD present"

echo "[3/4] Checking Harbor is reachable from backend pod..."
if ! kubectl -n lolday exec deploy/backend -- curl -sf -o /dev/null http://harbor.lolday.svc:80/api/v2.0/health 2>/dev/null; then
  echo "WARN: Harbor health check failed from backend pod. Deploy may still work; investigate if Phase 3 pipeline was OK."
else
  echo "  OK: Harbor reachable"
fi

echo "[4/4] Checking PostgreSQL is reachable..."
if ! kubectl -n lolday exec statefulset/postgresql -- pg_isready -U lolday 2>/dev/null; then
  echo "FAIL: PostgreSQL not ready."
  exit 1
fi
echo "  OK: PostgreSQL ready"

echo
echo "All pre-deploy checks passed (or acknowledged warnings)."
