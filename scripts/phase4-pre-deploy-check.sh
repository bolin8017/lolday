#!/usr/bin/env bash
set -euo pipefail

# Pre-flight checks for Phase 4 deploy.
# Confirms: sample dirs exist + readable, MLflow password set, Harbor is up.

# Default samples path. Keep in sync with `samples.hostPath` in
# charts/lolday/values.yaml. As of 2026-05-12 the path is a mergerfs union
# at /mnt/lolday-samples (NFS-backed); see
# docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md.
# Override via SAMPLES_DIR env if the chart value diverges locally.
SAMPLES_DIR=${SAMPLES_DIR:-/mnt/lolday-samples}

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
# The backend image is the slim Python runtime — no curl/wget. Use the
# in-venv httpx (the same client backend code uses for outbound HTTP) so
# this probe exercises the production code path. The previous curl-based
# check silently WARN'd because `curl` is not in PATH inside the
# container; the WARN was masking a no-op.
if HARBOR_OUT=$(kubectl -n lolday exec deploy/backend -- /app/.venv/bin/python -c "
import httpx, sys
try:
    r = httpx.get('http://harbor.lolday.svc:80/api/v2.0/health', timeout=5)
    print(f'HTTP={r.status_code}')
    sys.exit(0 if r.status_code == 200 else 1)
except Exception as e:
    print(f'error={type(e).__name__}: {e}')
    sys.exit(2)
" 2>&1); then
  echo "  OK: Harbor reachable (${HARBOR_OUT})"
else
  echo "  WARN: Harbor health check failed: ${HARBOR_OUT}"
  echo "  Deploy may still work; investigate if Phase 3 pipeline was OK."
fi

echo "[4/4] Checking PostgreSQL is reachable..."
if ! kubectl -n lolday exec statefulset/postgresql -- pg_isready -U lolday 2>/dev/null; then
  echo "FAIL: PostgreSQL not ready."
  exit 1
fi
echo "  OK: PostgreSQL ready"

echo
echo "All pre-deploy checks passed (or acknowledged warnings)."
