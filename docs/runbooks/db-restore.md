# PostgreSQL restore from MinIO backup

Source: #172, chart `pg-backup` CronJob, daily at 03:00 server time.

## Inventory

Backups live in the MinIO bucket `pg-backups`. Layout (per
`prodrigestivill/postgres-backup-local`):

```
pg-backups/
  daily/   # gz-compressed pg_dumpall output, keep 30 days
  weekly/  # one per week, keep 4
  monthly/ # one per month, keep 6
  last/    # symlink-style "latest of each cadence" (most recent)
```

Filename convention: `<DB>-<YYYY-MM-DD>-<HHMMSS>.sql.gz`.

## Restore procedure (full database)

For a planned restore (test environment refresh, schema reset, or DR
recovery) the safest path is to spin up a one-shot `psql` Pod in the
`lolday` namespace, stream the dump from MinIO over the in-cluster
network, and feed it into the live postgresql Service.

### 1. Identify the backup to restore

List the most recent daily backups:

```bash
kubectl -n lolday run -it --rm mc \
  --image=quay.io/minio/mc:RELEASE.2024-11-21T17-21-54Z \
  --restart=Never \
  --overrides='{"spec":{"automountServiceAccountToken":false}}' \
  --env=MINIO_ROOT_USER="$(kubectl -n lolday get secret minio-root-cred -o jsonpath='{.data.rootUser}' | base64 -d)" \
  --env=MINIO_ROOT_PASSWORD="$(kubectl -n lolday get secret minio-root-cred -o jsonpath='{.data.rootPassword}' | base64 -d)" \
  --command -- /bin/sh -c '
    mc alias set local http://lolday-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    mc ls local/pg-backups/daily/
  '
```

### 2. Stop application writes

Scale backend to zero so no client writes happen during the restore:

```bash
kubectl -n lolday scale deploy/backend --replicas=0
kubectl -n lolday scale deploy/mlflow --replicas=0
```

### 3. Restore

```bash
DUMP=lolday-2026-05-15-030000.sql.gz   # change to your target

kubectl -n lolday run -it --rm pg-restore \
  --image=postgres:16-alpine \
  --restart=Never \
  --overrides='{"spec":{"automountServiceAccountToken":false}}' \
  --env=POSTGRES_HOST=postgresql \
  --env=POSTGRES_USER="$(kubectl -n lolday get secret postgresql -o jsonpath='{.data.POSTGRES_USER}' | base64 -d)" \
  --env=PGPASSWORD="$(kubectl -n lolday get secret postgresql -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)" \
  --command -- /bin/sh -c '
    apk add --no-cache curl
    # MinIO is accessible in-cluster — use the pg-backup credential pair.
    AK="$(cat /run/secrets/pg/access-key)"
    SK="$(cat /run/secrets/pg/secret-key)"
    curl -sS -u "$AK:$SK" "http://lolday-minio:9000/pg-backups/daily/'"$DUMP"'" \
      | gunzip \
      | psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d postgres
  '
```

(For a production-grade procedure mount the `pg-backup-s3-cred` Secret
into the pod via a volume — the one-shot above hits the same path but
needs the secret pre-populated. The init-buckets-job creates that
Secret on first deploy.)

### 4. Resume traffic

```bash
kubectl -n lolday scale deploy/backend --replicas=1
kubectl -n lolday scale deploy/mlflow --replicas=1
```

Watch logs for clean boot:

```bash
kubectl -n lolday logs -f deploy/backend
```

## Verification

After restore, the backend's `_assert_schema_at_head()` will fail-fast
on lifespan if the dumped schema does not match the `head` Alembic
revision pinned in the running image. If it crashes:

1. The dump is from an older schema. Either restore to a backend
   image matching the dump's vintage, or apply Alembic forward to
   bring the schema up to head:
   ```bash
   kubectl -n lolday create job pg-restore-migrate \
     --from=job/lolday-alembic-upgrade
   ```
2. Confirm the alembic_version table now matches:
   ```bash
   kubectl -n lolday exec -it postgresql-0 -- \
     psql -U lolday -c 'SELECT * FROM alembic_version;'
   ```

## RPO / RTO

- RPO: 24h (daily backup; reduce schedule in `pgBackup.schedule` for
  tighter window).
- RTO: ~15min for a dataset under 100 MB (lab scale). pg_dumpall is
  single-threaded restore; large databases scale linearly.

## See also

- [`docs/architecture.md`](../architecture.md) §6 — storage layout
- [pgBackup values](../../charts/lolday/values.yaml) — `pgBackup.*`
- [prodrigestivill/postgres-backup-local](https://github.com/prodrigestivill/docker-postgres-backup-local) — image documentation
