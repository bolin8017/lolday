# Storage migration — filesystem PVC → MinIO S3

> **One-time migration**, executed during the 2026-05-11 storage redesign.
> If you are reading this and the migration has already happened (verify
> via `bash scripts/storage-audit.sh` showing non-zero object counts in
> `mlflow-artifacts` and `harbor-blobs` buckets), you do not need to run
> any of these. The runbook is kept here as historical record and as
> reference if a similar migration is ever needed.
>
> Reference:
>
> - Spec: `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md` §7
> - Plan: `docs/superpowers/plans/2026-05-11-storage-architecture-redesign.md`

## Pre-requisites

- MinIO StatefulSet healthy: `kubectl get pod -n lolday | grep minio` shows Running
- `mlflow-s3-cred`, `harbor-s3-cred`, `loki-s3-cred` K8s secrets exist
- Chart values for mlflow / harbor / loki are switched to S3 backend (Tasks 5, 8, 10 committed) **but** the running pods may still be on the OLD backend (waiting for cutover)

## Order (strict)

The three migrations are independent in data but spec §7.1 sequences them to bound blast radius:

### 1. MLflow (5 MB, ~10 seconds total, no downtime)

```bash
bash scripts/migrate-mlflow-to-s3.sh
kubectl rollout restart deployment/mlflow -n lolday
kubectl rollout status deployment/mlflow -n lolday --timeout=2m
```

Verify:

```bash
kubectl exec -n lolday deploy/backend -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://mlflow.lolday.svc:5000/api/2.0/mlflow/artifacts/list?run_id=<known-run-id>', timeout=10)
print(json.dumps(json.load(r), indent=2)[:400])
"
```

### 2. Harbor (25 GB, ~10–15 minutes, includes Harbor downtime)

```bash
bash scripts/migrate-harbor-to-s3.sh
```

The script handles scaling Harbor down, mc mirror copy (~10–15 min), helm upgrade with explicit S3 driver `--set` overrides, scaling Harbor back up.

Verify:

```bash
kubectl run harbor-pull-test -n lolday-jobs --rm -i --restart=Never \
  --image=harbor.lolday.svc:80/detectors/elf-rf:v4.2.0 \
  --command -- /bin/sh -c 'echo OK; exit 0'
```

### 3. Loki (chunk truncation, ~30 sec downtime)

Loki has 7-day retention. Old filesystem-backed chunks are abandoned at cutover; users see "no logs prior to cutover" for ~7 days, then indistinguishable from normal retention.

```bash
kubectl scale statefulset -n lolday loki --replicas=0
helm upgrade lolday charts/lolday -n lolday --reuse-values \
  --set loki.loki.storage.type=s3 \
  --set loki.loki.storage.bucketNames.chunks=loki-chunks \
  --set loki.loki.storage.bucketNames.ruler=loki-ruler \
  --set loki.loki.storage.s3.endpoint=lolday-minio.lolday.svc:9000 \
  --set loki.loki.storage.s3.region=us-east-1 \
  --set loki.loki.storage.s3.s3ForcePathStyle=true \
  --set loki.loki.storage.s3.insecure=true \
  --set loki.singleBinary.persistence.enabled=false
kubectl scale statefulset -n lolday loki --replicas=1
kubectl rollout status statefulset/loki -n lolday --timeout=3m
```

Verify:

```bash
sleep 60
MINIO_POD=$(kubectl get pods -n lolday | grep minio | awk '{print $1}')
kubectl exec -n lolday "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc ls --recursive local/loki-chunks/
```

## Smoke after all three steps

```bash
bash scripts/storage-audit.sh
```

Expected: all 4 buckets have non-zero object counts. Host disk free space should be slightly higher than before (filesystem PVCs no longer growing).

## Rollback per step

Each step is independently revertable via `helm rollback`:

```bash
helm rollback lolday <previous-revision>
```

> **Rolling back after legacy PVCs are deleted (Task 12) is one-way** — the
> filesystem data is gone. The 24-hour burn-in period between Phase 4 and
> Task 12 is specifically to provide a window where rollback is still possible.

## After all three steps succeed

Wait 24 hours, then proceed to plan Task 12 (delete legacy PVCs).

## Lessons learned during migration

These pitfalls were captured during the 2026-05-11 execution; the scripts now handle them:

1. **`quay.io/minio/mc` is distroless** — no `tar`, no `apk`. For PVC→MinIO copy, use `alpine:3.19 + apk add minio-client` (binary is `mcli` not `mc`).
2. **MinIO pod runs as UID 65534 (nobody)** — set `MC_CONFIG_DIR=/tmp/mc` so `mc alias set` doesn't fail trying to write to `/.mc`.
3. **`helm upgrade --reuse-values` does NOT pick up chart-default changes** — values from the previous release are kept. To apply config changes from `values.yaml`, either use `bash scripts/deploy.sh` (full re-render) OR explicit `--set` overrides on top of `--reuse-values`.
4. **Loki StatefulSet `volumeClaimTemplates` is immutable** — when flipping `persistence.enabled: false`, must `kubectl delete statefulset loki` before the helm upgrade.
5. **Harbor existingSecret expects literal env-var-style key names** — `REGISTRY_STORAGE_S3_ACCESSKEY` / `REGISTRY_STORAGE_S3_SECRETKEY` (NOT `access-key` / `secret-key`).
6. **MLflow's official `ghcr.io/mlflow/mlflow:v2.20.3` skinny image lacks `boto3`** — needed for S3 artifact store. We extended the image with `boto3==1.38.5` and tagged it `:v2.20.3-boto3`.
