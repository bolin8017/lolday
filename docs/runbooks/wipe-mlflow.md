# Wipe MLflow runbook

> Operator runbook for `scripts/wipe-mlflow-history.sh`. **Irreversible.** Read the entire page before running. SSH safety hard rule does not apply (no host changes), but data-loss safety does — back up before continuing.

## 1. What it does

Soft-deletes every MLflow resource the platform tracks, then runs `mlflow gc` inside the mlflow pod to permanently purge them and reclaim artifact storage:

| Step                                     | Effect                                                                                                                           |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 1/4 — runs/delete                        | Marks every active run as `deleted` (lifecycle stage). Already-soft-deleted runs are skipped — `gc` purges both.                 |
| 2/4 — registered-models / model-versions | Hard-deletes all registry entries (versions first, then the shell).                                                              |
| 3/4 — experiments/delete                 | Soft-deletes every experiment except `Default` (id=0), which the API refuses to delete.                                          |
| 4/4 — `mlflow gc`                        | Permanently removes soft-deleted runs from the Postgres backing store and unlinks their artefacts on the `/mlflow-artifacts` PV. |

The script does **not** touch:

- The lolday Postgres DB (the `job` / `job_events` / `model_version` / `model_transition_log` / `detector_version` tables) — wipe those separately if a full reset is needed; `model_version` and `detector_version.mlflow_experiment_id` are the only foreign-key referrers into MLflow's data, so leaving them populated will leave dangling references that the platform's reconciler will eventually project as orphans.
- The MLflow artifact PVC itself (`mlflow-artifacts`, 100 Gi). `gc` empties it but the PVC stays bound.

## 2. When to use

**Yes:**

- Full-platform reset (e.g. switching tenants, dev rebuild before a demo).
- Schema-breaking maldet upgrade where `model_version` rows are no longer compatible with the new label encoding (cutover scenario — see `docs/superpowers/plans/2026-05-02-maldet-2-and-runs-cleanup.md` §4.6 for the canonical worked example).
- An MLflow corruption incident where Postgres rows reference artefacts that no longer exist on disk (or vice versa).

**No:**

- Reclaiming disk on a healthy installation. Use the MLflow UI to selectively delete experiments instead.
- Cleaning up failed runs from one detector. Use experiment-level deletion via the UI or the REST API.

## 3. Pre-requisites

### Tools on the operator host

- `kubectl` with access to the `lolday` namespace.
- `jq` and `curl` in `$PATH` (install via `bash scripts/install-tools.sh` if absent).

### Cluster state

- The mlflow Deployment must be running. The script auto-discovers the pod via the `app.kubernetes.io/component=mlflow` label.
- The `MLFLOW_BACKEND_STORE_URI` env var on the mlflow container — wired in by the chart since v0.16.3 (see `charts/lolday/templates/mlflow.yaml`); the `gc` step fails without it.

### Backups

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)

# 1. Postgres dump (covers MLflow + lolday DBs in one shot)
kubectl -n lolday exec postgresql-0 -- pg_dumpall -U lolday > "$HOME/backup-pgdump-$TS.sql"

# 2. MLflow artifact tarball
MLFLOW_POD=$(kubectl -n lolday get pod -l app.kubernetes.io/component=mlflow -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday exec "$MLFLOW_POD" -- tar -cz -C /mlflow-artifacts . > "$HOME/backup-mlflow-artifacts-$TS.tar.gz"

# 3. Verify counts match before/after (file count of backup vs pod)
tar tzf "$HOME/backup-mlflow-artifacts-$TS.tar.gz" | grep -v '/$' | wc -l
kubectl -n lolday exec "$MLFLOW_POD" -- sh -c 'find /mlflow-artifacts -type f | wc -l'
```

Keep the backups for at least 7 days; longer if the wipe is part of a regulatory-impacting change.

## 4. Quiesce platform writes

`POST /api/v1/jobs` is the only request path that creates new MLflow state. Flip it off before wiping so a job that lands mid-wipe doesn't write into a half-cleared store:

```bash
kubectl -n lolday set env deployment/backend BACKEND_MAINTENANCE_MODE=1
kubectl -n lolday rollout status deployment/backend
```

Verify the gate is live (a syntactically-valid POST should now return 503 + `Retry-After`):

```bash
# Use a CF Access service token or a real SSO session — see
# .lolday-cf-svctoken.env for the operator's existing token.
curl -sS -i -X POST -H "Content-Type: application/json" \
  -H "Cookie: CF_Authorization=<jwt>" \
  -d '{"type":"train","detector_version_id":"00000000-0000-0000-0000-000000000000","train_dataset_id":"00000000-0000-0000-0000-000000000000"}' \
  https://lolday.connlabai.com/api/v1/jobs
# Expected: HTTP/2 503; retry-after: 3600
```

## 5. Run the script

```bash
bash scripts/wipe-mlflow-history.sh
```

The script:

1. Discovers the mlflow pod, sets up a kubectl port-forward to the in-cluster service on port 15000 (override with `PF_PORT=…` if 15000 is taken), and tears the tunnel down on exit (success or error).
2. Counts existing experiments / runs / registered models and prints the totals.
3. Prompts `Continue? Type 'yes' to proceed:` — typing anything else aborts cleanly without any deletes.
4. Runs the four steps above.

Expected duration: ~30 s for a typical install (a few dozen experiments, low-hundreds of runs). `gc` dominates if the artifact volume is large.

## 6. Verify

```bash
# Active experiments — only the Default shell should remain
kubectl -n lolday exec "$MLFLOW_POD" -- sh -c '
  curl -fsS "http://localhost:5000/api/2.0/mlflow/experiments/search?max_results=1000" \
    | jq ".experiments | map({experiment_id, name})"'
# Expected: [{"experiment_id":"0","name":"Default"}]

# Soft-deleted experiments should also be gone (gc purges them)
kubectl -n lolday exec "$MLFLOW_POD" -- sh -c '
  curl -fsS "http://localhost:5000/api/2.0/mlflow/experiments/search?max_results=1000&view_type=ALL" \
    | jq ".experiments | length"'
# Expected: 1

# Artifact volume size
kubectl -n lolday exec "$MLFLOW_POD" -- du -sh /mlflow-artifacts
# Expected: a few hundred KB at most (filesystem metadata only)
```

## 7. Resume platform writes

```bash
kubectl -n lolday set env deployment/backend BACKEND_MAINTENANCE_MODE=0
kubectl -n lolday rollout status deployment/backend
```

If the wipe was part of a larger cutover (e.g. detector schema break), the next step is typically to re-trigger detector image builds and submit a baseline train/evaluate/predict per detector to verify end-to-end. The full canonical sequence is captured in `docs/superpowers/plans/2026-05-02-maldet-2-and-runs-cleanup.md` §4.

## 8. Recovery

If anything goes wrong mid-wipe and the cluster ends up in a partially-cleaned state:

1. **Postgres**: restore from `$HOME/backup-pgdump-$TS.sql` via `kubectl exec postgresql-0 -- psql -U lolday < backup-pgdump-$TS.sql`. The dump uses `pg_dumpall` which restores cluster-wide users and both databases.
2. **MLflow artefacts**: extract the tarball back into `/mlflow-artifacts` via `kubectl exec mlflow-…  -- tar -xz -C /mlflow-artifacts < backup-mlflow-artifacts-$TS.tar.gz`. The container runs as a non-root user; if extraction fails on permissions, copy the tarball into the pod first and extract from inside.
3. Restart the mlflow Deployment so it re-reads the restored DB / FS state: `kubectl -n lolday rollout restart deploy/mlflow`.
