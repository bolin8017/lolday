#!/usr/bin/env bash
# Harbor blob migration: registry PVC filesystem → MinIO S3.
# Brief Harbor downtime (~10–15 min) required to avoid in-flight push corruption.
#
# Implementation notes:
#   - MinIO image is distroless — no shell/tar available inside the pod.
#     A temporary alpine:3.19 copier pod mounts the registry PVC read-only
#     and uses apk-installed minio-client (mcli binary) to mirror directly
#     to MinIO over the cluster network. This avoids the kubectl cp + mcli pipe
#     round-trip used in migrate-mlflow-to-s3.sh (that was necessary because
#     MLflow artifacts are small; Harbor blobs are ~25 GB — piping each file
#     is too slow).
#   - IMPORTANT: Alpine's `mc` apk package is Midnight Commander (a file
#     manager), NOT MinIO client. The correct package is `minio-client` (binary
#     name `mcli`). mcli works without a TTY; mc requires terminal settings.
#   - runAsUser: 0 in the copier pod is required: Harbor registry writes blobs
#     as UID 10000; the alpine default non-root user cannot read them.
#   - The copier mounts the PVC read-only so Harbor can safely re-mount it
#     (though Harbor is scaled down during the copy for consistency).
#
# Spec: docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md §6.5
set -euo pipefail

NS=lolday
COPIER_POD=harbor-blob-copier

echo "[pre-check 1] verify harbor-s3-cred has the required S3 keys"
KEYS=$(kubectl get secret -n "$NS" harbor-s3-cred -o jsonpath='{.data}' \
  | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).keys()))")
case "$KEYS" in
  *REGISTRY_STORAGE_S3_ACCESSKEY*)
    echo "  ok — keys present: $KEYS" ;;
  *)
    echo "  FATAL: harbor-s3-cred missing required keys (have: $KEYS)" >&2
    exit 1 ;;
esac

echo "[pre-check 2] confirm no active jobs in lolday-jobs"
# grep exits 1 when no lines match — use || true to avoid set -e triggering
JOB_PODS=$(kubectl get pods -n lolday-jobs --no-headers 2>/dev/null \
  | { grep -v Completed || true; } \
  | { grep -v Evicted   || true; } \
  | wc -l)
if [ "$JOB_PODS" -gt 0 ]; then
  echo "  WARNING: $JOB_PODS active pods in lolday-jobs — Harbor scale-down will interrupt any in-progress pulls." >&2
  echo "  Continuing anyway (operator should have confirmed no critical jobs)." >&2
else
  echo "  ok — lolday-jobs is empty"
fi

echo "[step 1] scale Harbor down to avoid in-flight push corruption"
kubectl scale deployment -n "$NS" lolday-harbor-core       --replicas=0
kubectl scale deployment -n "$NS" lolday-harbor-jobservice --replicas=0
kubectl scale deployment -n "$NS" lolday-harbor-registry   --replicas=0

echo "[step 1] waiting for Harbor registry pods to terminate..."
while [ "$(kubectl get pods -n "$NS" -l app.kubernetes.io/component=registry \
           --no-headers 2>/dev/null | wc -l)" -gt 0 ]; do
  sleep 3
done
echo "  ok — registry pods terminated"

echo "[step 2] launch temporary alpine copier pod (mounts registry PVC read-only)"
# Delete any leftover copier from a previous run
kubectl delete pod -n "$NS" "$COPIER_POD" --ignore-not-found --wait=true 2>/dev/null || true

kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $COPIER_POD
  namespace: $NS
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 0          # need root to read Harbor PVC files (written as UID 10000)
  containers:
    - name: copier
      image: alpine:3.19
      command:
        - /bin/sh
        - -c
        - apk add --no-cache minio-client && echo "mcli ready" && sleep 7200
      env:
        - name: ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: harbor-s3-cred
              key: REGISTRY_STORAGE_S3_ACCESSKEY
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: harbor-s3-cred
              key: REGISTRY_STORAGE_S3_SECRETKEY
      volumeMounts:
        - name: src
          mountPath: /src
          readOnly: true
  volumes:
    - name: src
      persistentVolumeClaim:
        claimName: lolday-harbor-registry
EOF

echo "[step 2] waiting for copier pod to become Running..."
kubectl wait -n "$NS" --for=condition=Ready --timeout=3m "pod/$COPIER_POD"

echo "[step 2] waiting for mc to be installed inside the copier..."
for i in $(seq 1 24); do
  if kubectl exec -n "$NS" "$COPIER_POD" -- which mcli >/dev/null 2>&1; then
    echo "  ok — mcli is available"
    break
  fi
  if [ "$i" -eq 24 ]; then
    echo "  FATAL: mcli not found after 120 s" >&2
    exit 1
  fi
  sleep 5
done

echo "[step 3] inventory source PVC"
kubectl exec -n "$NS" "$COPIER_POD" -- du -sh /src/docker/registry/v2 2>&1 | head -3
FS_BYTES=$(kubectl exec -n "$NS" "$COPIER_POD" -- du -sb /src/docker/registry/v2 | awk '{print $1}')
echo "  source bytes: $FS_BYTES"

echo "[step 4] configure mcli alias (using ACCESS_KEY / SECRET_KEY env from secret)"
# mcli (minio-client) works without a TTY; mc (Midnight Commander) does not — use mcli
kubectl exec -n "$NS" "$COPIER_POD" -- \
  sh -c 'mcli alias set dst http://lolday-minio:9000 "$ACCESS_KEY" "$SECRET_KEY"'

echo "[step 5] mirror registry tree to MinIO (may take 10–15 min for ~25 GB)"
START_TS=$(date +%s)
kubectl exec -n "$NS" "$COPIER_POD" -- \
  sh -c 'mcli mirror --overwrite \
    /src/docker/registry/v2/ \
    dst/harbor-blobs/docker/registry/v2/'
END_TS=$(date +%s)
ELAPSED=$(( END_TS - START_TS ))
echo "  mirror done in ${ELAPSED}s"

echo "[step 6] verify byte-count parity (allow 1% tolerance for metadata)"
S3_BYTES=$(kubectl exec -n "$NS" "$COPIER_POD" -- \
  sh -c 'mcli du --json dst/harbor-blobs/ | tail -1' \
  | python3 -c "import json,sys; print(json.loads(sys.stdin.read().strip()).get('size', 0))")
echo "  FS bytes : $FS_BYTES"
echo "  S3 bytes : $S3_BYTES"
THRESHOLD=$(( FS_BYTES * 99 / 100 ))
if [ "$S3_BYTES" -lt "$THRESHOLD" ]; then
  echo "  FATAL: S3 copy is short ($S3_BYTES < 99% of $FS_BYTES)" >&2
  exit 1
fi
echo "  ok — byte-count parity verified"

echo "[step 7] clean up copier pod"
kubectl delete pod -n "$NS" "$COPIER_POD" --wait=false

echo "[step 8] bring Harbor back up with S3-backed registry config"
# Use explicit --set to override the stale imageChartStorage.type=filesystem that
# is baked into the deployed release's merged values (from the Harbor sub-chart's
# own defaults). --reuse-values alone will NOT apply this change because the
# sub-chart default was merged in at an earlier deployment and is now part of the
# release's "user values" snapshot — see CLAUDE.md memory on helm upgrade state carry.
helm upgrade lolday charts/lolday -n "$NS" --reuse-values \
  --set harbor.persistence.imageChartStorage.type=s3 \
  --set harbor.persistence.imageChartStorage.disableredirect=true \
  --set "harbor.persistence.imageChartStorage.s3.region=us-east-1" \
  --set harbor.persistence.imageChartStorage.s3.bucket=harbor-blobs \
  --set "harbor.persistence.imageChartStorage.s3.regionendpoint=http://lolday-minio.lolday.svc:9000" \
  --set harbor.persistence.imageChartStorage.s3.existingSecret=harbor-s3-cred \
  --set harbor.persistence.imageChartStorage.s3.skipverify=true \
  --set harbor.persistence.imageChartStorage.s3.v4auth=true \
  --set harbor.persistence.imageChartStorage.s3.secure=false \
  2>&1 | tail -5

kubectl scale deployment -n "$NS" lolday-harbor-core       --replicas=1
kubectl scale deployment -n "$NS" lolday-harbor-jobservice --replicas=1
kubectl scale deployment -n "$NS" lolday-harbor-registry   --replicas=1

echo "[step 8] waiting for Harbor to become healthy..."
kubectl rollout status -n "$NS" deployment/lolday-harbor-core     --timeout=3m
kubectl rollout status -n "$NS" deployment/lolday-harbor-registry --timeout=3m
echo "  ok — Harbor pods are running"

echo "[step 9] post-migration sanity: confirm registry /etc/registry/config.yml shows S3"
kubectl exec -n "$NS" deploy/lolday-harbor-registry -c registry -- \
  grep -A 5 "storage:" /etc/registry/config.yml | head -10
echo "  (expected: 's3:' block, not 'filesystem:')"

echo "[done] Harbor blob migration complete — registry now backed by MinIO S3"
