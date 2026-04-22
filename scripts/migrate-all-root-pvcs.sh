#!/bin/bash
# Phase 9.6 — orchestrate root-LV PVC migration → /mnt/ssd500g.
#
# Runs `migrate-ephemeral-to-ssd.sh STAGE=5` for the six remaining root-LV
# PVCs in risk-ascending order. Fail-fast: any step error halts the run and
# leaves `${OLD_PATH}.old` in place so Stage 5's rollback hint still works.
#
# Order + rationale:
#   1. lolday-grafana              (5Gi PVC, 4K actual)       Deployment — tiny, lowest risk
#   2. mlflow-artifacts            (100Gi PVC, 252K actual)   Deployment — near-empty
#   3. storage-loki-0              (30Gi PVC, 46M actual)     StatefulSet — log store, non-critical
#   4. data-postgresql-0           (10Gi PVC, 72M actual)     StatefulSet — backend+mlflow DB (brief outage)
#   5. prometheus-kps-prometheus-0 (20Gi PVC, 437M actual)    StatefulSet — TSDB (brief metrics gap)
#   6. lolday-harbor-registry      (100Gi PVC, 14G actual)    Deployment — biggest, last
#
# Usage: sudo bash scripts/migrate-all-root-pvcs.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (sudo)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE5="$SCRIPT_DIR/migrate-ephemeral-to-ssd.sh"
SSD=/mnt/ssd500g

if [ ! -x "$STAGE5" ] && ! bash -n "$STAGE5" 2>/dev/null; then
  echo "ERROR: $STAGE5 not found or not parseable." >&2
  exit 1
fi
if ! mountpoint -q "$SSD"; then
  echo "ERROR: $SSD is not mounted." >&2
  exit 1
fi

# PVCs as parallel arrays (bash 3-compatible; no associative array). Each
# index i across the six arrays is one migration:
#   LABEL  — human-readable step name (printed in the banner)
#   NS     — namespace
#   PVC    — PVC name
#   WL     — WORKLOAD=<kind>/<name> passed to Stage 5
#   SEL    — READY_SELECTOR label-expr for the scale-up readiness gate
#            (empty string → let Stage 5 auto-infer for StatefulSets)
LABELS=(
  "grafana"
  "mlflow"
  "loki"
  "postgres"
  "prometheus"
  "harbor-registry"
)
NSS=(
  "lolday"
  "lolday"
  "lolday"
  "lolday"
  "monitoring"
  "lolday"
)
PVCS=(
  "lolday-grafana"
  "mlflow-artifacts"
  "storage-loki-0"
  "data-postgresql-0"
  "prometheus-kps-prometheus-db-prometheus-kps-prometheus-0"
  "lolday-harbor-registry"
)
WLS=(
  "deployment/lolday-grafana"
  "deployment/mlflow"
  "statefulset/loki"
  "statefulset/postgresql"
  "statefulset/prometheus-kps-prometheus"
  "deployment/lolday-harbor-registry"
)
SELS=(
  "-l app.kubernetes.io/name=grafana,app.kubernetes.io/instance=lolday"
  "-l app.kubernetes.io/component=mlflow"
  ""  # auto-infer for statefulset
  ""  # auto-infer for statefulset
  ""  # auto-infer for statefulset
  "-l app=harbor,component=registry"
)

N=${#LABELS[@]}

echo "==============================================================="
echo "Phase 9.6 — migrate $N root-LV PVCs to $SSD/k3s-storage"
echo "==============================================================="
echo
echo "Pre-flight:"
df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'
echo

# Verify every PVC is still on root LV + target path doesn't exist. If any
# fails here, no data moves — safe to abort.
echo "[pre-flight] checking PVC paths + target collisions…"
for i in $(seq 0 $((N-1))); do
  NS=${NSS[$i]}
  PVC=${PVCS[$i]}
  PV=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}' 2>/dev/null || true)
  if [ -z "$PV" ]; then
    echo "  FATAL: $NS/$PVC has no bound PV (does the PVC exist?)" >&2
    exit 1
  fi
  OLD=$(kubectl get pv "$PV" -o jsonpath='{.spec.local.path}' 2>/dev/null || true)
  case "$OLD" in
    /var/lib/rancher/k3s/storage/*)
      NEW_BASE=$(basename "$OLD")
      NEW="$SSD/k3s-storage/$NEW_BASE"
      if [ -e "$NEW" ]; then
        echo "  FATAL: $NEW already exists — a prior run left residue" >&2
        echo "        (if safe, rm -rf $NEW and re-run)" >&2
        exit 1
      fi
      printf "  %-18s %-55s OK\n" "${LABELS[$i]}" "$OLD"
      ;;
    /mnt/ssd500g/*)
      printf "  %-18s already on SSD — will skip\n" "${LABELS[$i]}"
      ;;
    *)
      echo "  FATAL: unexpected PV path for $NS/$PVC: $OLD" >&2
      exit 1 ;;
  esac
done
echo "  all checks green."
echo

START=$(date +%s)
for i in $(seq 0 $((N-1))); do
  STEP=$((i+1))
  NS=${NSS[$i]}
  PVC=${PVCS[$i]}
  PV=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}')
  OLD=$(kubectl get pv "$PV" -o jsonpath='{.spec.local.path}')
  if [[ "$OLD" == /mnt/ssd500g/* ]]; then
    echo "=== [$STEP/$N] ${LABELS[$i]} — already on SSD, skipping ==="
    continue
  fi
  echo
  echo "==============================================================="
  echo "=== [$STEP/$N] ${LABELS[$i]}  (PVC=$PVC)"
  echo "==============================================================="
  STEP_START=$(date +%s)

  # Stage 5 inherits env we export plus NS/PVC/WORKLOAD/READY_SELECTOR.
  NS="$NS" \
  PVC="$PVC" \
  WORKLOAD="${WLS[$i]}" \
  READY_SELECTOR="${SELS[$i]}" \
  STAGE=5 \
  bash "$STAGE5"

  printf ">>> step %d/%d done in %ds\n" "$STEP" "$N" "$(( $(date +%s) - STEP_START ))"
done

echo
echo "==============================================================="
echo "all $N migrations complete in $(( $(date +%s) - START ))s"
echo "==============================================================="
df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'
echo
echo "Verify workloads end-to-end, then free space by removing the"
echo "shelved originals (each Stage 5 left one behind):"
echo
echo "  sudo rm -rf /var/lib/rancher/k3s/storage/pvc-*.old"
echo
echo "Do not run the rm until you've spot-checked:"
echo "  kubectl -n lolday get pods"
echo "  kubectl -n monitoring get pods"
echo "  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c 'select count(*) from detector;'"
echo "  (Harbor UI / MLflow / Grafana / Prometheus all reachable)"
