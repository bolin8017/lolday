#!/usr/bin/env bash
# One-shot MLflow artifact migration: PVC filesystem → MinIO S3.
# Idempotent: already-uploaded files are overwritten in-place (versioning keeps history).
#
# Implementation notes vs. original plan:
#   - The MinIO image is distroless — no tar/gzip available inside the pod.
#   - We therefore: (1) kubectl cp PVC contents to a local tmpdir (MLflow pod has GNU tar),
#     then (2) pipe each file individually to MinIO using `mc pipe` via kubectl exec -i.
#   - mc pipe reads from stdin; idempotency relies on object versioning rather than ETag skip.
#     Versioning means re-running creates new versions but does not duplicate current state.
#   - MC_CONFIG_DIR=/tmp/mc required — MinIO pod runs as non-root UID 65534.
#   - MinIO pod selector: app=minio,release=lolday (NOT app.kubernetes.io/name=minio).
#   - mc binary: /usr/bin/mc (not /usr/local/bin/mc in this image).
#
# Spec: docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md §6.5
set -euo pipefail

NS=lolday

echo "==> Wait for MinIO ready"
kubectl wait -n $NS --for=condition=ready --timeout=2m \
  pod -l app=minio,release=lolday

MINIO_POD="$(kubectl get pod -n $NS -l app=minio,release=lolday -o name | head -1)"
echo "==> MinIO pod: $MINIO_POD"

echo "==> Configure mc alias in MinIO pod"
ROOT_USER=$(kubectl get secret -n $NS minio-root-cred -o jsonpath='{.data.rootUser}' | base64 -d)
ROOT_PASS=$(kubectl get secret -n $NS minio-root-cred -o jsonpath='{.data.rootPassword}' | base64 -d)
kubectl exec -n $NS $MINIO_POD -- \
  env MC_CONFIG_DIR=/tmp/mc /usr/bin/mc alias set local http://lolday-minio:9000 "$ROOT_USER" "$ROOT_PASS"

MLFLOW_POD="$(kubectl get pod -n $NS -l app.kubernetes.io/component=mlflow -o name | head -1)"
echo "==> MLflow pod: $MLFLOW_POD"

echo "==> Inventory: how many files in /mlflow-artifacts?"
FS_COUNT=$(kubectl exec -n $NS $MLFLOW_POD -- find /mlflow-artifacts -type f 2>/dev/null | wc -l)
echo "FS files: $FS_COUNT"

if [ "$FS_COUNT" -eq 0 ]; then
  echo "==> No files on PVC — nothing to migrate."
else
  # Stage PVC contents on local disk via kubectl cp (uses tar on MLflow pod side).
  TMPDIR="$(mktemp -d /tmp/mlflow-migrate-XXXXXX)"
  trap 'rm -rf "$TMPDIR"' EXIT

  echo "==> Staging PVC to local $TMPDIR via kubectl cp"
  # kubectl cp uses <pod>:/path/. to avoid including the top-level dir name in the copy
  kubectl cp -n $NS "${MLFLOW_POD#pod/}:/mlflow-artifacts/." "$TMPDIR/"

  echo "==> Uploading files to MinIO via mc pipe"
  find "$TMPDIR" -type f | while IFS= read -r LOCAL_FILE; do
    # Compute the relative object key (strip tmpdir prefix, keep leading /)
    REL="${LOCAL_FILE#$TMPDIR/}"
    echo "  uploading: $REL"
    kubectl exec -i -n $NS $MINIO_POD -- \
      env MC_CONFIG_DIR=/tmp/mc /usr/bin/mc pipe "local/mlflow-artifacts/$REL" \
      < "$LOCAL_FILE"
  done

  echo "==> Verify object count in S3 matches FS"
  S3_COUNT=$(kubectl exec -n $NS $MINIO_POD -- \
    env MC_CONFIG_DIR=/tmp/mc /usr/bin/mc ls --recursive local/mlflow-artifacts/ 2>/dev/null | wc -l)
  echo "FS files: $FS_COUNT, S3 objects: $S3_COUNT"
  test "$FS_COUNT" = "$S3_COUNT" || { echo "FATAL: count mismatch"; exit 1; }
fi

echo "==> done"
