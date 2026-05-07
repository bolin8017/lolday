#!/usr/bin/env bash
# Idempotent migration: rename MLflow experiments and rewrite run tags
# from the pre-v0.21 UUID-based naming to the v0.21 owner/detector
# namespace. Matches the convention adopted by the v0.20.0 model registry
# (`{owner_handle}/{detector_name}`) so every MLflow primitive a user sees
# uses the same human-readable hierarchy.
#
# Old experiment name : detector:<detector_uuid>:<git_tag>
# New experiment name : <owner_handle>/<detector_name>/<git_tag>
#
# Old run tags (values were UUIDs)
#   lolday.user              = <user_uuid>
#   lolday.detector_version  = <detector_version_uuid>
# New run tags (UUIDs move to *_id companions; primary value is human-readable)
#   lolday.user              = <owner_handle>
#   lolday.user_id           = <user_uuid>
#   lolday.detector_version  = <detector_name>/<git_tag>
#   lolday.detector_version_id = <detector_version_uuid>
#
# Sudo-free; uses the cluster-internal MLflow service via curl + jq
# through a kubectl port-forward, plus kubectl exec into postgresql-0
# for the detector → owner_handle lookup.
#
# Usage:
#   bash scripts/migrate-mlflow-experiment-naming.sh
#
# Override:
#   NAMESPACE=lolday MLFLOW_API=http://localhost:5001/api/2.0/mlflow \
#     bash scripts/migrate-mlflow-experiment-naming.sh
#
# Idempotent: experiments already on the new namespace are skipped; runs
# whose tags already carry handle-form values are skipped.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
PG_POD="${PG_POD:-postgresql-0}"
PF_PORT="${PF_PORT:-15010}"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required (install via scripts/install-tools.sh)" >&2
  exit 1
fi

if [ -z "${MLFLOW_API:-}" ]; then
  echo "[setup] starting port-forward to svc/mlflow on :$PF_PORT"
  kubectl -n "$NAMESPACE" port-forward "svc/mlflow" "${PF_PORT}:5000" \
    >/tmp/migrate-mlflow-pf.log 2>&1 &
  PF_PID=$!
  trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 1 30); do
    sleep 0.3
    if curl -sS -o /dev/null "http://localhost:${PF_PORT}/health"; then
      break
    fi
  done
  MLFLOW_API="http://localhost:${PF_PORT}/api/2.0/mlflow"
fi

echo "[step 1] searching experiments with legacy naming"
EXPS_JSON=$(curl -sS -X POST -H 'Content-Type: application/json' \
  -d '{"max_results":1000,"view_type":"ALL"}' \
  "${MLFLOW_API}/experiments/search")

LEGACY_EXPS=$(echo "$EXPS_JSON" | jq -c '.experiments[] | select(.name | startswith("detector:"))')

if [ -z "$LEGACY_EXPS" ]; then
  echo "[step 1] no experiments with legacy 'detector:UUID:tag' naming — nothing to rename"
else
  echo "[step 2] renaming experiments"
  while IFS= read -r exp; do
    EXP_ID=$(echo "$exp" | jq -r '.experiment_id')
    OLD_NAME=$(echo "$exp" | jq -r '.name')
    DETECTOR_ID=$(echo "$OLD_NAME" | awk -F: '{print $2}')
    GIT_TAG=$(echo "$OLD_NAME" | awk -F: '{print $3}')

    if [ -z "$DETECTOR_ID" ] || [ -z "$GIT_TAG" ]; then
      echo "  skip exp=$EXP_ID name=$OLD_NAME (unparseable)" >&2
      continue
    fi

    # Two single-column queries — avoids tab-vs-tr-d-space pitfalls in
    # multi-column psql output piping.
    HANDLE=$(kubectl -n "$NAMESPACE" exec "$PG_POD" -- \
      psql -U lolday -d lolday -tA -c \
      "SELECT u.handle FROM detector d JOIN \"user\" u ON u.id = d.owner_id WHERE d.id = '${DETECTOR_ID}';" \
      2>/dev/null | tr -d '[:space:]' || true)
    DET_NAME=$(kubectl -n "$NAMESPACE" exec "$PG_POD" -- \
      psql -U lolday -d lolday -tA -c \
      "SELECT name FROM detector WHERE id = '${DETECTOR_ID}';" \
      2>/dev/null | tr -d '[:space:]' || true)

    if [ -z "$HANDLE" ] || [ -z "$DET_NAME" ]; then
      echo "  skip exp=$EXP_ID name=$OLD_NAME (detector $DETECTOR_ID not in DB; likely orphan)" >&2
      continue
    fi

    NEW_NAME="${HANDLE}/${DET_NAME}/${GIT_TAG}"
    echo "  rename exp=$EXP_ID '$OLD_NAME' -> '$NEW_NAME'"

    HTTP_CODE=$(curl -sS -o /tmp/mlflow-rename.json -w '%{http_code}' \
      -X POST -H 'Content-Type: application/json' \
      -d "{\"experiment_id\":\"${EXP_ID}\",\"new_name\":\"${NEW_NAME}\"}" \
      "${MLFLOW_API}/experiments/update")
    if [ "$HTTP_CODE" != "200" ]; then
      echo "  ERROR rename failed for exp=$EXP_ID (http=$HTTP_CODE):" >&2
      cat /tmp/mlflow-rename.json >&2
      exit 1
    fi
  done <<< "$LEGACY_EXPS"
fi

echo "[step 3] rewriting run tags across all experiments"
ALL_EXPS=$(echo "$EXPS_JSON" | jq -r '.experiments[].experiment_id')
TAG_TOUCHED=0
for EXP_ID in $ALL_EXPS; do
  # Active runs only — soft-deleted runs are not user-facing and MLflow's
  # set-tag API is a no-op against them, which would loop forever.
  RUNS_JSON=$(curl -sS -X POST -H 'Content-Type: application/json' \
    -d "{\"experiment_ids\":[\"${EXP_ID}\"],\"max_results\":1000,\"run_view_type\":\"ACTIVE_ONLY\"}" \
    "${MLFLOW_API}/runs/search")
  RUN_COUNT=$(echo "$RUNS_JSON" | jq '.runs // [] | length')
  if [ "$RUN_COUNT" = "0" ]; then
    continue
  fi

  while IFS= read -r run; do
    RUN_ID=$(echo "$run" | jq -r '.info.run_id')
    USER_TAG=$(echo "$run" | jq -r '.data.tags[]? | select(.key=="lolday.user") | .value')
    DV_TAG=$(echo "$run" | jq -r '.data.tags[]? | select(.key=="lolday.detector_version") | .value')

    UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    USER_NEEDS=0
    DV_NEEDS=0
    [[ "$USER_TAG" =~ $UUID_RE ]] && USER_NEEDS=1
    [[ "$DV_TAG"   =~ $UUID_RE ]] && DV_NEEDS=1
    if [ $USER_NEEDS -eq 0 ] && [ $DV_NEEDS -eq 0 ]; then
      continue
    fi

    RUN_TOUCHED=0
    if [ $USER_NEEDS -eq 1 ]; then
      HANDLE=$(kubectl -n "$NAMESPACE" exec "$PG_POD" -- \
        psql -U lolday -d lolday -tA -c \
        "SELECT handle FROM \"user\" WHERE id = '${USER_TAG}';" \
        | tr -d '[:space:]')
      if [ -z "$HANDLE" ]; then
        echo "  skip run=$RUN_ID (user $USER_TAG not in DB)" >&2
      else
        curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
          -d "{\"run_id\":\"${RUN_ID}\",\"key\":\"lolday.user_id\",\"value\":\"${USER_TAG}\"}" \
          "${MLFLOW_API}/runs/set-tag"
        curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
          -d "{\"run_id\":\"${RUN_ID}\",\"key\":\"lolday.user\",\"value\":\"${HANDLE}\"}" \
          "${MLFLOW_API}/runs/set-tag"
        RUN_TOUCHED=1
      fi
    fi

    if [ $DV_NEEDS -eq 1 ]; then
      DV_LABEL=$(kubectl -n "$NAMESPACE" exec "$PG_POD" -- \
        psql -U lolday -d lolday -tA -c \
        "SELECT d.name || '/' || dv.git_tag FROM detector_version dv JOIN detector d ON d.id = dv.detector_id WHERE dv.id = '${DV_TAG}';" \
        | tr -d '[:space:]')
      if [ -z "$DV_LABEL" ]; then
        echo "  skip run=$RUN_ID (detector_version $DV_TAG not in DB)" >&2
      else
        curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
          -d "{\"run_id\":\"${RUN_ID}\",\"key\":\"lolday.detector_version_id\",\"value\":\"${DV_TAG}\"}" \
          "${MLFLOW_API}/runs/set-tag"
        curl -sS -o /dev/null -X POST -H 'Content-Type: application/json' \
          -d "{\"run_id\":\"${RUN_ID}\",\"key\":\"lolday.detector_version\",\"value\":\"${DV_LABEL}\"}" \
          "${MLFLOW_API}/runs/set-tag"
        RUN_TOUCHED=1
      fi
    fi
    TAG_TOUCHED=$((TAG_TOUCHED + RUN_TOUCHED))
  done < <(echo "$RUNS_JSON" | jq -c '.runs[]')
done

echo "[done] rewrote tags on $TAG_TOUCHED run(s)"
