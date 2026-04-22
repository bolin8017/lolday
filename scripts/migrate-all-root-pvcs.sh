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

# Classify every PVC's current state — Stage 5 handles all three:
#   A. Fresh:       OLD exists, NEW absent                       → full migration
#   B. Migrated:    OLD is a bind-mount                          → skip
#   C. Half-done:   OLD absent, ${OLD}.old present, NEW present  → resume at mount step
# Any other shape aborts here so the operator can inspect before we touch data.
echo "[pre-flight] classifying PVC states…"
for i in $(seq 0 $((N-1))); do
  NS=${NSS[$i]}
  PVC=${PVCS[$i]}
  PV=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}' 2>/dev/null || true)
  if [ -z "$PV" ]; then
    echo "  FATAL: $NS/$PVC has no bound PV (does the PVC exist?)" >&2
    exit 1
  fi
  OLD=$(kubectl get pv "$PV" -o jsonpath='{.spec.local.path}' 2>/dev/null || true)
  STATE="?"
  case "$OLD" in
    /mnt/ssd500g/*)
      # PV was re-provisioned directly on SSD (e.g. after a retention-policy-
      # induced PVC delete + fresh StatefulSet-driven claim that landed on
      # the Phase-9 local-path default). Nothing to migrate.
      STATE="D-already-on-ssd" ;;
    /var/lib/rancher/k3s/storage/*) ;;
    *)
      echo "  FATAL: unexpected PV path for $NS/$PVC: $OLD" >&2
      exit 1 ;;
  esac
  NEW_BASE=$(basename "$OLD")
  NEW="$SSD/k3s-storage/$NEW_BASE"
  if [ "$STATE" = "?" ]; then
    if mountpoint -q "$OLD" 2>/dev/null; then
      STATE="B-migrated"
    elif [ -d "$OLD" ] && [ ! -e "$NEW" ]; then
      STATE="A-fresh"
    elif [ ! -e "$OLD" ] && [ -d "${OLD}.old" ] && [ -d "$NEW" ]; then
      STATE="C-resume"
    else
      echo "  FATAL: unexpected on-disk state for $NS/$PVC" >&2
      echo "    OLD ($OLD): $( [ -e "$OLD" ] && echo exists || echo absent )" >&2
      echo "    NEW ($NEW): $( [ -e "$NEW" ] && echo exists || echo absent )" >&2
      echo "    ${OLD}.old: $( [ -e "${OLD}.old" ] && echo exists || echo absent )" >&2
      echo "    mount: $( mountpoint -q "$OLD" 2>/dev/null && echo yes || echo no )" >&2
      exit 1
    fi
  fi
  printf "  %-18s %s\n" "${LABELS[$i]}" "$STATE"
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
  # Skip when the PV is already on SSD. Two sub-cases:
  #   B-migrated: OLD is a bind-mount inside /var/lib/rancher/k3s/storage/
  #   D-already-on-ssd: OLD itself lives under /mnt/ssd500g/ (fresh PV)
  if mountpoint -q "$OLD" 2>/dev/null; then
    echo "=== [$STEP/$N] ${LABELS[$i]} — $OLD already bind-mounted, skipping ==="
    continue
  fi
  case "$OLD" in
    /mnt/ssd500g/*)
      echo "=== [$STEP/$N] ${LABELS[$i]} — PV already on SSD ($OLD), skipping ==="
      continue ;;
  esac
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
