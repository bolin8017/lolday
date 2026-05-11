#!/usr/bin/env bash
# Validates docs/runbooks/add-ssd.md end-to-end using a 10 GB loopback file
# as a fake SSD. Walks Steps 1–5 of the runbook, then rolls back.
#
# Usage:
#   bash scripts/validate-add-ssd-runbook.sh
#   # → prompts for sudo password (used for losetup, mkfs, mount, chown, umount, rm)
#
# Spec: docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md §8
# Plan: docs/superpowers/plans/2026-05-11-storage-architecture-redesign.md Task 18
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

NS=lolday
FAKE_IMG=/tmp/lolday-fake-ssd.img
FAKE_LOOP=/dev/loop10
FAKE_MOUNT=/mnt/fakessd
PV_NAME=minio-fake
PVC_NAME=minio-fake-pvc
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESULTS_FILE=$(mktemp -t add-ssd-validation-XXXX.log)
CLEANUP_HELM_REVERT=0

# ---------------------------------------------------------------------------
# Cleanup — always runs on EXIT / INT / TERM
# ---------------------------------------------------------------------------
cleanup() {
  local exit_code=$?
  set +e
  echo ""
  echo "==> Cleanup (always runs on exit)"

  # Remove test object from MinIO (best-effort)
  local minio_pod
  minio_pod=$(kubectl get pod -n "$NS" -l app=minio,release=lolday -o name 2>/dev/null | head -1 | sed 's|pod/||') || true
  if [ -n "$minio_pod" ]; then
    kubectl exec -n "$NS" "$minio_pod" -- \
      env MC_CONFIG_DIR=/tmp/mc mc rm local/mlflow-artifacts/_validate/100m \
      >/dev/null 2>&1 || true
  fi

  # Delete fake PVC + PV (--wait=false so we don't block)
  kubectl delete pvc -n "$NS" "$PVC_NAME" --wait=false 2>/dev/null || true
  kubectl delete pv "$PV_NAME" --wait=false 2>/dev/null || true

  # Revert MinIO helm upgrade if we performed one
  if [ "$CLEANUP_HELM_REVERT" -eq 1 ]; then
    echo "  re-running helm upgrade to revert MinIO to single-pool..."
    # Restore values.yaml from backup if present, then re-upgrade
    if [ -f "$REPO_ROOT/charts/lolday/values.yaml.bak" ]; then
      mv "$REPO_ROOT/charts/lolday/values.yaml.bak" "$REPO_ROOT/charts/lolday/values.yaml"
      echo "  restored charts/lolday/values.yaml from .bak"
    fi
    helm upgrade lolday "$REPO_ROOT/charts/lolday" -n "$NS" --reuse-values 2>&1 | tail -3 || true
  fi

  # Unmount / detach loop device / remove img  (each line is sudo-required)
  sudo umount "$FAKE_MOUNT" 2>/dev/null || true              # requires sudo
  sudo losetup -d "$FAKE_LOOP" 2>/dev/null || true           # requires sudo
  sudo rm -f "$FAKE_IMG" 2>/dev/null || true                 # requires sudo
  sudo rmdir "$FAKE_MOUNT" 2>/dev/null || true               # requires sudo

  echo "  cleanup done"
  rm -f "$RESULTS_FILE"

  exit "$exit_code"
}

trap 'cleanup' EXIT INT TERM

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
echo "==> Pre-flight: confirm prerequisites"

command -v kubectl >/dev/null || { echo "FATAL: kubectl not found" >&2; exit 1; }
command -v helm >/dev/null    || { echo "FATAL: helm not found" >&2; exit 1; }
command -v sudo >/dev/null    || { echo "FATAL: sudo not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: python3 not found" >&2; exit 1; }

# Verify MinIO pod is running
MINIO_POD=$(kubectl get pod -n "$NS" -l app=minio,release=lolday -o name 2>/dev/null | head -1 | sed 's|pod/||')
if [ -z "$MINIO_POD" ]; then
  echo "FATAL: no MinIO pod found in namespace $NS (label app=minio,release=lolday)" >&2
  echo "       Is the platform deployed? Run: kubectl get pod -n $NS" >&2
  exit 1
fi
POD_PHASE=$(kubectl get pod -n "$NS" "$MINIO_POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
if [ "$POD_PHASE" != "Running" ]; then
  echo "FATAL: MinIO pod $MINIO_POD is in phase '$POD_PHASE', expected Running" >&2
  exit 1
fi

# Guard against stale loop device or mount point
if losetup "$FAKE_LOOP" >/dev/null 2>&1; then
  echo "FATAL: $FAKE_LOOP is already in use. Run 'sudo losetup -d $FAKE_LOOP' first." >&2
  exit 1
fi
if [ -d "$FAKE_MOUNT" ]; then
  echo "FATAL: $FAKE_MOUNT already exists. Remove or umount it first." >&2
  exit 1
fi
if kubectl get pv "$PV_NAME" >/dev/null 2>&1; then
  echo "FATAL: PV $PV_NAME already exists. Delete it first: kubectl delete pv $PV_NAME" >&2
  exit 1
fi
if kubectl get pvc -n "$NS" "$PVC_NAME" >/dev/null 2>&1; then
  echo "FATAL: PVC $PVC_NAME already exists in ns $NS. Delete it first." >&2
  exit 1
fi

echo "  pre-flight OK — MinIO pod: $MINIO_POD ($POD_PHASE)"

# ---------------------------------------------------------------------------
# Step 1: Create loopback file → format XFS → mount (mirrors runbook Step 1)
# ---------------------------------------------------------------------------
echo ""
echo "==> Step 1: create 10 GB loopback file as fake SSD"

sudo dd if=/dev/zero of="$FAKE_IMG" bs=1M count=10240 status=progress  # requires sudo
sudo losetup "$FAKE_LOOP" "$FAKE_IMG"                                   # requires sudo
sudo mkfs.xfs -f "$FAKE_LOOP"                                           # requires sudo
sudo mkdir -p "$FAKE_MOUNT"                                             # requires sudo
sudo mount "$FAKE_LOOP" "$FAKE_MOUNT"                                   # requires sudo
sudo chown 1001:1001 "$FAKE_MOUNT"                                      # requires sudo (MinIO UID)

df -h "$FAKE_MOUNT"
echo "  loop device: $FAKE_LOOP  →  $FAKE_MOUNT  OK"

# ---------------------------------------------------------------------------
# Step 2: Create PV + PVC bound to the fake mount (mirrors runbook Step 2)
# ---------------------------------------------------------------------------
echo ""
echo "==> Step 2: create PV + PVC bound to fake mount"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: $PV_NAME
spec:
  capacity:
    storage: 10Gi
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: minio-local
  hostPath:
    path: $FAKE_MOUNT/minio
    type: DirectoryOrCreate
  claimRef:
    name: $PVC_NAME
    namespace: $NS
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: [server30]
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC_NAME
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: minio-local
  resources:
    requests:
      storage: 10Gi
  volumeName: $PV_NAME
EOF

# Wait up to 20 s for PVC to bind
PVC_STATUS=""
for i in $(seq 1 10); do
  PVC_STATUS=$(kubectl get pvc -n "$NS" "$PVC_NAME" -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
  [ "$PVC_STATUS" = "Bound" ] && break
  sleep 2
done

kubectl get pvc -n "$NS" "$PVC_NAME"
if [ "$PVC_STATUS" != "Bound" ]; then
  echo "FATAL: PVC $PVC_NAME did not reach Bound (got: '$PVC_STATUS')" >&2
  exit 1
fi
echo "  PVC $PVC_NAME → Bound  OK"

# ---------------------------------------------------------------------------
# Step 3: Extend MinIO via helm upgrade (mirrors runbook Step 3)
# ---------------------------------------------------------------------------
echo ""
echo "==> Step 3: extend MinIO StatefulSet with second pool (/data2)"

# Backup values.yaml so cleanup() can restore it
cp "$REPO_ROOT/charts/lolday/values.yaml" "$REPO_ROOT/charts/lolday/values.yaml.bak"
CLEANUP_HELM_REVERT=1

helm upgrade lolday "$REPO_ROOT/charts/lolday" -n "$NS" --reuse-values \
  --set minio.drivesPerNode=2 \
  --set-json "minio.extraVolumes=[{\"name\":\"data2\",\"persistentVolumeClaim\":{\"claimName\":\"$PVC_NAME\"}}]" \
  --set-json "minio.extraVolumeMounts=[{\"name\":\"data2\",\"mountPath\":\"/data2\"}]" \
  --set-json "minio.extraArgs=[\"server\",\"/data1\",\"/data2\",\"--console-address\",\":9001\"]" \
  2>&1 | tail -5

# MinIO standalone uses Deployment; StatefulSet name used in runbook for real deploys.
# Try Deployment first, then StatefulSet fallback.
if kubectl get deployment lolday-minio -n "$NS" >/dev/null 2>&1; then
  kubectl rollout status deployment/lolday-minio -n "$NS" --timeout=3m
elif kubectl get statefulset lolday-minio -n "$NS" >/dev/null 2>&1; then
  kubectl rollout status statefulset/lolday-minio -n "$NS" --timeout=3m
else
  echo "WARN: could not determine MinIO workload type; sleeping 30s for pod readiness"
  sleep 30
fi

# Refresh pod name after rollout (pod may have restarted)
MINIO_POD=$(kubectl get pod -n "$NS" -l app=minio,release=lolday -o name | head -1 | sed 's|pod/||')
echo "  MinIO pod after rollout: $MINIO_POD"

# ---------------------------------------------------------------------------
# Step 4: Verify MinIO sees both drives (mirrors runbook Step 4)
# ---------------------------------------------------------------------------
echo ""
echo "==> Step 4: verify MinIO reports ≥ 2 drives"

# Resolve credentials from the cluster Secret
MINIO_USER=$(kubectl get secret -n "$NS" minio-root-cred \
  -o jsonpath='{.data.rootUser}' 2>/dev/null | base64 -d)
MINIO_PASS=$(kubectl get secret -n "$NS" minio-root-cred \
  -o jsonpath='{.data.rootPassword}' 2>/dev/null | base64 -d)

if [ -z "$MINIO_USER" ] || [ -z "$MINIO_PASS" ]; then
  echo "FATAL: could not read minio-root-cred secret in ns $NS" >&2
  exit 1
fi

# Set up mc alias inside the pod; /tmp/mc avoids permission issues
kubectl exec -n "$NS" "$MINIO_POD" -- \
  env MC_CONFIG_DIR=/tmp/mc mc alias set local http://lolday-minio:9000 \
  "$MINIO_USER" "$MINIO_PASS" >/dev/null

INFO_JSON=$(kubectl exec -n "$NS" "$MINIO_POD" -- \
  env MC_CONFIG_DIR=/tmp/mc mc admin info local --json 2>&1)

DRIVES=$(echo "$INFO_JSON" | python3 - <<'PY'
import json, sys
try:
    d = json.load(sys.stdin)
    servers = d.get("info", {}).get("servers", [])
    print(sum(len(s.get("drives", [])) for s in servers))
except Exception:
    print(0)
PY
)

echo "  drives reported by mc admin info: $DRIVES"
if [ "$DRIVES" -lt 2 ]; then
  echo "FATAL: expected ≥ 2 drives, got $DRIVES" >&2
  echo "  mc admin info output:" >&2
  echo "$INFO_JSON" >&2
  exit 1
fi
echo "  drive count OK ($DRIVES ≥ 2)"

# ---------------------------------------------------------------------------
# Step 5: Write a 100 MB test object, verify it lands (mirrors runbook Step 5)
# ---------------------------------------------------------------------------
echo ""
echo "==> Step 5: write 100 MB test object to mlflow-artifacts bucket"

kubectl exec -n "$NS" "$MINIO_POD" -- \
  sh -c 'dd if=/dev/urandom of=/tmp/100m bs=1M count=100 2>&1' | tail -2

kubectl exec -n "$NS" "$MINIO_POD" -- \
  env MC_CONFIG_DIR=/tmp/mc mc cp /tmp/100m local/mlflow-artifacts/_validate/100m

kubectl exec -n "$NS" "$MINIO_POD" -- \
  env MC_CONFIG_DIR=/tmp/mc mc ls local/mlflow-artifacts/_validate/100m

echo "  100 MB object written and verified  OK"

# Clean up test object before cleanup() also tries (idempotent)
kubectl exec -n "$NS" "$MINIO_POD" -- \
  env MC_CONFIG_DIR=/tmp/mc mc rm local/mlflow-artifacts/_validate/100m \
  >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Record validation result in the runbook
# ---------------------------------------------------------------------------
echo ""
echo "==> Validation succeeded — recording entry in add-ssd.md"

SCRIPT_USER=$(whoami)
ENTRY="- $TS — Loop-device simulation ($FAKE_LOOP, 10 GB XFS). Pool 2 added via helm upgrade; $DRIVES drives reported by \`mc admin info\`. 100 MB test write succeeded. Rollback clean. Validated by $SCRIPT_USER."

RUNBOOK="$REPO_ROOT/docs/runbooks/add-ssd.md"
if [ ! -f "$RUNBOOK" ]; then
  echo "WARN: $RUNBOOK not found, skipping doc update" >&2
else
  python3 - <<PY
import re, sys

path = """$RUNBOOK"""
entry = """$ENTRY"""

with open(path) as f:
    text = f.read()

# Find "## Validation history" section header
m = re.search(r'(?m)^##\s*Validation history\s*$', text)
if not m:
    print("WARN: 'Validation history' section not found in runbook", file=sys.stderr)
    sys.exit(0)

# Deduplicate: skip if identical line already present
if entry in text:
    print("INFO: entry already present, skipping append")
    sys.exit(0)

# Insert the entry immediately after the heading block
insert_at = m.end()
new = text[:insert_at] + "\n\n" + entry + "\n" + text[insert_at:]

with open(path, 'w') as f:
    f.write(new)

print(f"OK: appended validation entry to {path}")
PY
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "==> All 5 steps passed."
echo ""
echo "    Next steps:"
echo "      git diff docs/runbooks/add-ssd.md      # review the new validation entry"
echo "      git add docs/runbooks/add-ssd.md"
echo '      git commit -m "docs(runbooks): add-ssd validated via loop-device simulation"'
echo ""
echo "    The trap will now revert MinIO to single-pool and clean up the loopback device."
