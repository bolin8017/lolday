#!/bin/bash
# Phase 9.6 — clean up root-LV shelves left by migrate-all-root-pvcs.sh.
#
# After migration is verified healthy, this script:
#   1. Re-verifies every migrated pod is reading from /dev/nvme0n1p1 (SSD)
#      — fail-fast if anything still points at root LV, since deleting a
#      shelf that is still the live data would be catastrophic.
#   2. Removes the six `.old` shelves under /var/lib/rancher/k3s/storage/
#      (grafana 4K / mlflow 252K / postgres ~72M / prometheus ~437M /
#       harbor-registry ~14G / loki-old ~46M).
#   3. Removes the stale Loki bind-mount that survived the retention-policy
#      recovery (PVC storage-loki-0 was recreated against a fresh PV on
#      SSD; the old pvc-2b4c46f4-... bind mount + fstab entry are orphans).
#
# Usage: sudo bash scripts/cleanup-migrated-shelves.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (sudo)." >&2
  exit 1
fi

SSD=/mnt/ssd500g
SSD_DEV=/dev/nvme0n1p1

# Parallel arrays: workload + its in-pod mount path + backing PVC UID prefix.
# We verify each pod sees $SSD_DEV at its mount path before touching any shelf.
LABELS=(
  "grafana"
  "mlflow"
  "postgres"
  "loki"
  "prometheus"
  "harbor-registry"
)
# ns  workload                                    pod-internal path           PVC UID (for the .old shelf to rm)
CHECKS=(
  "lolday      deploy/lolday-grafana          /var/lib/grafana              pvc-94bc4a37-bfd3-4b89-94a7-5628f918eaf9_lolday_lolday-grafana"
  "lolday      deploy/mlflow                  /mlflow-artifacts             pvc-e13c10c6-3560-49f2-9f40-6a4d8ed5e868_lolday_mlflow-artifacts"
  "lolday      postgresql-0                   /var/lib/postgresql/data      pvc-ed5d1b49-b0ad-449f-b36b-426ce12b60c9_lolday_data-postgresql-0"
  "lolday      loki-0                         /var/loki                     pvc-2b4c46f4-127e-4cd6-bb30-22d0d8b3b4d8_lolday_storage-loki-0"
  "monitoring  prometheus-kps-prometheus-0    /prometheus                   pvc-e32ce086-4c0a-4b14-9914-262f87d43820_monitoring_prometheus-kps-prometheus-db-prometheus-kps-prometheus-0"
  "lolday      deploy/lolday-harbor-registry  /storage                      pvc-09639c28-7418-4f11-921d-35a8504992d1_lolday_lolday-harbor-registry"
)
# container flag per workload (for kubectl exec -c X; grafana + prometheus + harbor have sidecars)
EXEC_C=(
  "grafana"
  ""
  ""
  ""
  "prometheus"
  "registry"
)

echo "=== before ==="
df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'
echo

echo "[pre-flight] confirming each pod is on $SSD_DEV…"
for i in "${!LABELS[@]}"; do
  read -r NS WL IN_POD_PATH UID_BASE <<<"${CHECKS[$i]}"
  C_FLAG=()
  [ -n "${EXEC_C[$i]}" ] && C_FLAG=(-c "${EXEC_C[$i]}")
  DEV=$(kubectl -n "$NS" exec "${C_FLAG[@]}" "$WL" -- df "$IN_POD_PATH" 2>/dev/null \
    | awk 'END{print $1}')
  if [ "$DEV" != "$SSD_DEV" ]; then
    echo "  FATAL: ${LABELS[$i]} ($NS/$WL $IN_POD_PATH) still on $DEV — refusing to delete shelf." >&2
    echo "  If this is unexpected, investigate before re-running (shelf is the last copy)." >&2
    exit 1
  fi
  printf "  %-18s %s  OK\n" "${LABELS[$i]}" "$DEV"
done
echo

echo "[1/3] removing .old shelves on root LV…"
for i in "${!LABELS[@]}"; do
  read -r _NS _WL _PATH UID_BASE <<<"${CHECKS[$i]}"
  SHELF="/var/lib/rancher/k3s/storage/${UID_BASE}.old"
  if [ -d "$SHELF" ]; then
    SZ=$(du -sh "$SHELF" 2>/dev/null | awk '{print $1}')
    echo "  rm $SHELF ($SZ)"
    rm -rf "$SHELF"
  else
    echo "  (already absent) $SHELF"
  fi
done
echo

echo "[2/3] removing stale Loki bind mount + fstab entry…"
# The migration retention-policy recovery left behind a bind-mount + fstab
# entry for pvc-2b4c46f4-... even though Loki's current PV is pvc-5fa72c8c-...
# (fresh, SSD-native). Drop the orphan so /proc/mounts + fstab stay truthful.
LOKI_OLD_BASE="pvc-2b4c46f4-127e-4cd6-bb30-22d0d8b3b4d8_lolday_storage-loki-0"
LOKI_OLD_MNT="/var/lib/rancher/k3s/storage/$LOKI_OLD_BASE"
LOKI_OLD_SRC="$SSD/k3s-storage/$LOKI_OLD_BASE"

if mountpoint -q "$LOKI_OLD_MNT" 2>/dev/null; then
  echo "  umount $LOKI_OLD_MNT"
  umount "$LOKI_OLD_MNT"
fi
if [ -d "$LOKI_OLD_MNT" ]; then
  rmdir "$LOKI_OLD_MNT" 2>/dev/null || true
fi
if grep -qF "$LOKI_OLD_BASE" /etc/fstab; then
  echo "  prune /etc/fstab entry for $LOKI_OLD_BASE"
  sed -i "\|k3s-storage/$LOKI_OLD_BASE |d" /etc/fstab
fi
if [ -d "$LOKI_OLD_SRC" ]; then
  echo "  rm $LOKI_OLD_SRC"
  rm -rf "$LOKI_OLD_SRC"
fi
echo

echo "[3/3] final state"
df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'
echo
ACTIVE_BIND=$(grep -c 'k3s/storage/pvc-' /proc/mounts || echo 0)
FSTAB_BIND=$(grep -c 'k3s-storage/pvc-' /etc/fstab || echo 0)
echo "  active k3s-storage bind mounts: $ACTIVE_BIND (expected 5)"
echo "  fstab k3s-storage bind entries: $FSTAB_BIND (expected 5)"
