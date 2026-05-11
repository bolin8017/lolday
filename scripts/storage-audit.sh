#!/usr/bin/env bash
# Print a summary of MinIO storage usage per bucket, plus host-disk free.
# Use periodically (or attach to a CronJob) to track growth.
#
# Spec: docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md
set -euo pipefail

NS=lolday
MINIO_POD="$(kubectl get pods -n "$NS" 2>/dev/null | grep -i minio | awk '{print $1}' | head -1)"

if [ -z "$MINIO_POD" ]; then
  echo "FATAL: no MinIO pod found in namespace $NS" >&2
  exit 1
fi

echo "==> MinIO bucket usage"
kubectl exec -n "$NS" "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc du --recursive local/ 2>&1 | head -10 || true

echo ""
echo "==> MinIO cluster capacity"
kubectl exec -n "$NS" "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc admin info local --json 2>&1 | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    servers = d.get('info', {}).get('servers', [])
    for s in servers:
        for drive in s.get('drives', []):
            avail = drive.get('availableSpace', 0)
            used  = drive.get('usedSpace', 0)
            total = drive.get('totalSpace', 0)
            print(f\"  drive {drive.get('endpoint','?')}: used={used/1e9:.1f}G / total={total/1e9:.1f}G ({100*used//max(total,1)}%)\")
except Exception as e:
    print(f'  could not parse: {e}')
" || true

echo ""
echo "==> Object count + age per bucket"
for bkt in mlflow-artifacts harbor-blobs loki-chunks loki-ruler; do
  COUNT=$(kubectl exec -n "$NS" "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc ls --recursive "local/$bkt/" 2>&1 | wc -l) || COUNT=0
  OLDEST=$(kubectl exec -n "$NS" "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc find "local/$bkt/" --print '{time}' 2>/dev/null | sort | head -1 || echo "(empty)") || OLDEST="(error)"
  NEWEST=$(kubectl exec -n "$NS" "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc find "local/$bkt/" --print '{time}' 2>/dev/null | sort | tail -1 || echo "(empty)") || NEWEST="(error)"
  [ -z "$OLDEST" ] && OLDEST="(empty)"
  [ -z "$NEWEST" ] && NEWEST="(empty)"
  printf "  %-20s  count=%d  oldest=%s  newest=%s\n" "$bkt" "$COUNT" "$OLDEST" "$NEWEST"
done

echo ""
echo "==> Host disk on server30 (via backend pod)"
kubectl exec -n "$NS" deploy/backend -- df -h / 2>&1 | head -3 || true
