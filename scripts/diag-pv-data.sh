#!/bin/bash
# Check whether the local-path PV data is still physically on disk.
# Run as root.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo" >&2; exit 1
fi

STORAGE=/var/lib/rancher/k3s/storage

echo "=== all pvc dirs on disk ==="
ls -la "$STORAGE" 2>&1 | head -20

echo
echo "=== per-PV data check ==="
for PVC in lolday_lolday-harbor-registry \
           lolday_database-data-lolday-harbor-database-0 \
           lolday_data-postgresql-0 \
           lolday_mlflow-artifacts \
           lolday_data-lolday-harbor-redis-0; do
  echo "--- $PVC ---"
  for d in "$STORAGE"/*"${PVC}"*; do
    [ -d "$d" ] || continue
    echo "  $d"
    du -sh "$d" 2>/dev/null
    ls "$d" 2>&1 | head -5
  done
done

echo
echo "=== mount table near pods ==="
mount | grep -E "harbor-registry|harbor-database|postgresql-0|mlflow-artifacts" | head -10

echo
echo "=== check kubelet volume bind-mount ==="
# Kubelet re-creates these bind mounts from pod spec on each pod start.
# If a pod references PVC X but kubelet mounts it from a FRESH path, the
# pod sees empty fs even though the real data is elsewhere.
KUBELET_PODS=/var/lib/kubelet/pods
# Find the harbor-registry pod dir
for pd in "$KUBELET_PODS"/*; do
  POD_UID=$(basename "$pd")
  # match against k8s state
  if kubectl -n lolday get pods --no-headers 2>/dev/null | awk '{print $1}' | while read -r p; do
    kubectl -n lolday get pod "$p" -o jsonpath='{.metadata.uid}'; echo
  done | grep -q "$POD_UID"; then
    if [ -d "$pd/volumes" ]; then
      echo "--- pod $POD_UID ---"
      ls "$pd/volumes/" 2>&1 | head -5
    fi
  fi
done 2>&1 | head -40
