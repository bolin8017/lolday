# Phase 4: Dataset & Jobs — Design Specification

## Overview

Phase 4 closes the loop from a built detector image (Phase 3) to actual **train / evaluate / predict** jobs running on GPUs against curated malware datasets, with results tracked in MLflow and models managed through MLflow Model Registry.

**Goal:** A developer / user selects a detector version + dataset config + hyperparameters, submits a job, and gets back reproducible artifacts (trained model / metrics / predictions) tracked in MLflow — without ever touching CLI or YAML.

**Constraints:**

- Must not break SSH on server30 (port 9453)
- No custom code where an open-source tool exists
- Training pod isolation must exceed Phase 3 build pod (malware samples are executable-by-nature, jobs must never have egress or K8s API access)
- Single server for now (2× RTX 2080 Ti); design must not preclude multi-node scaling
- Prefer modifying the maldet framework (user-owned) over carrying platform-side glue code

---

## Scope

Phase 4 covers main spec §5 (Dataset Management) + §6 (Train/Eval/Predict Workflow) + §7 (Results & Model Management) as one cohesive delivery:

1. **Dataset storage infrastructure** — malware/benign sample folders exposed as PVs, PVC abstraction, read-only mount
2. **Dataset Config CRUD** — CSV-manifest-based dataset configs with checksum + file-existence validation
3. **MLflow deployment** — tracking server + Model Registry, backed by PostgreSQL + PV for artifacts
4. **maldet framework upgrade** — add MLflow integration to `maldet.cli` so all detectors auto-log without per-detector changes
5. **Job submission + lifecycle** — POST `/jobs`, config rendering, K8s Job creation, reconciler-driven state machine
6. **Job execution pod** — detector image + standardized mounts + strict security + deny-all-egress NetworkPolicy
7. **Model Registry UX** — thin lolday endpoints that proxy MLflow Model Registry API for Staging/Production/Archived transitions
8. **Experiment / run listing** — lolday endpoints that proxy MLflow listing API for UI use (frontend comes in Phase 5)

**Out of scope (deferred):**

- **Frontend forms** for hyperparameter input (Phase 5) — Phase 4 APIs accept raw JSON
- **Email notifications** on job completion (Phase 6 — Phase 4 only writes audit logs + exposes hook points)
- **Loki log aggregation** (Phase 6) — Phase 4 streams via K8s API + stores `log_tail` in DB, same pattern as Phase 3 builds
- **Hyperparameter search** (grid/random/Bayesian) — Future
- **Multi-GPU distributed training** / gang scheduling — Future (Volcano/Kueue)
- **GPU time-slicing / MPS** — Future
- **Model drift detection** — Future
- **Cloudflare Tunnel / external exposure** — Phase 6
- **MinIO for oversized dataset CSVs** — YAGNI; Phase 4 caps inline CSV at 10MB; raise cap / add MinIO when a real user hits the limit

---

## Architecture

```
                          User (User/Developer/Admin)
                                  │
                                  ▼ HTTPS (Phase 6 Cloudflare Tunnel)
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI Backend  (namespace: lolday)                             │
│                                                                  │
│  Dataset Config                                                  │
│  POST   /datasets                        → upload CSV, validate  │
│  GET    /datasets                        → list                  │
│  GET    /datasets/{id}                   → metadata + checksum   │
│  POST   /datasets/{id}/clone             → fork                  │
│  DELETE /datasets/{id}                   → soft delete           │
│                                                                  │
│  Jobs                                                            │
│  POST   /jobs                            → submit (type+refs)    │
│  GET    /jobs                            → list / filter         │
│  GET    /jobs/{id}                       → status + log_tail     │
│  GET    /jobs/{id}/logs                  → live K8s log proxy    │
│  POST   /jobs/{id}/cancel                → delete K8s Job        │
│                                                                  │
│  MLflow Proxy                                                    │
│  GET    /experiments                     → list runs (paginated) │
│  GET    /experiments/{id}/runs           → per-experiment runs   │
│  GET    /runs/{id}                       → single run detail     │
│  GET    /runs/{id}/artifacts/{path}      → artifact download     │
│                                                                  │
│  Model Registry                                                  │
│  GET    /models                          → registered models     │
│  GET    /models/{name}/versions          → versions + stages     │
│  POST   /models/{name}/versions/{v}/transition → stage change    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ JobReconciler (asyncio loop, 10s)                           │  │
│  │  → polls K8s Job status, updates DB                         │  │
│  │  → on success: read run_id → query MLflow → summary to DB   │  │
│  │  → on failure: extract reason, tail logs                    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
         │ K8s API                              ▲ REST (HTTP)
         ▼ (namespace-scoped Role)              │
┌───────────────────────────────────────┐   ┌──────────────────────┐
│ K8s Job: job-{type}-{id}              │   │ MLflow Server        │
│                                       │   │ (ns: lolday)         │
│  Volumes (all rootfs read-only):      │──►│  - Deployment ×1     │
│   • samples-pvc   (RO, hostPath→NFS)  │   │  - PV (artifacts)    │
│   • config        (emptyDir w/ JSON)  │   │  - PostgreSQL schema │
│   • output        (emptyDir w/ model) │   │  - ClusterIP svc     │
│   • source-model  (downloaded if eval │   └──────┬───────────────┘
│                   or predict)         │          │
│                                       │          │
│  env: MLFLOW_TRACKING_URI,            │          │ log_params /
│       MLFLOW_RUN_ID, MLFLOW_EXP_ID    │          │ log_metrics /
│                                       │          │ log_artifacts
│  command: [<detector_cli>,            │          │
│            <action>,                  │          │
│            --config, /cfg/config.json]│          │
│                                       │          │
│  SecurityContext:                     │          │
│   • runAsNonRoot=true                 │          │
│   • readOnlyRootFilesystem=true       │          │
│   • capabilities.drop=[ALL]           │          │
│   • seccompProfile=RuntimeDefault     │          │
│   • automountServiceAccountToken=false│          │
│                                       │          │
│  NetworkPolicy (namespace: lolday):   │          │
│   • Ingress: deny all                 │          │
│   • Egress: kube-dns + MLflow only    │          │
│             (deny internet, deny      │          │
│             Harbor, deny backend,     │          │
│             deny K8s API)             │          │
│                                       │          │
│  resources.limits.nvidia.com/gpu: 1   │          │
│  activeDeadlineSeconds: <per-type>    │          │
└─────────────┬─────────────────────────┘          │
              │ hostPath                           │
              ▼ (RO + noexec)                      │
     ┌────────────────────┐                        │
     │ /data/malware-     │                        │
     │     samples/       │                        │
     │ /data/benign-      │                        │
     │     samples/       │                        │
     │ (server30 host fs) │                        │
     └────────────────────┘                        │
                                                   │
                  ┌────────────────────────────────┘
                  ▼
          ┌──────────────────┐
          │ PostgreSQL       │
          │  • lolday schema │ ← backend
          │  • mlflow schema │ ← MLflow server
          └──────────────────┘
```

---

## Dataset Storage

### Sample folder layout (on server30)

```
/data/
├── malware-samples/
│   ├── 00/
│   │   ├── 0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11
│   │   ├── 00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35
│   │   └── ...
│   ├── 01/
│   │   └── ...
│   └── ff/
└── benign-samples/
    └── <same 2-char hex prefix structure>
```

- File names are full SHA256 hex digests (64 chars)
- First two hex chars determine sub-directory (256 buckets, ~3900 files/bucket at 1M scale)
- Read-only from platform's perspective; write access belongs to the human curator process (outside Phase 4)
- Ownership / mode: readable by `bolin8017` user (same UID that runs K3s)
- Storage requirement: 300-400 GB for malware; benign sized on first population (see Open Questions)
- Target scale: ~1M malware samples + unknown benign count

### PersistentVolume abstraction

One PV per sample category, backed by `hostPath` for Phase 4, with PVC interface stable for future migration to NFS CSI.

```yaml
# PV: hostPath-backed, ReadOnly
apiVersion: v1
kind: PersistentVolume
metadata:
  name: malware-samples
  labels: { app.kubernetes.io/name: malware-samples }
spec:
  capacity: { storage: 500Gi } # loose upper bound; hostPath ignores this
  accessModes: [ReadOnlyMany]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: "" # manual binding, no provisioner
  hostPath:
    path: /data/malware-samples
    type: Directory
  nodeAffinity: # pin to server30 (anticipates multi-node)
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - { key: kubernetes.io/hostname, operator: In, values: [server30] }
```

```yaml
# PVC: claim in lolday namespace
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: malware-samples, namespace: lolday }
spec:
  accessModes: [ReadOnlyMany]
  storageClassName: ""
  volumeName: malware-samples
  resources: { requests: { storage: 500Gi } }
```

Analogous PV/PVC for `benign-samples`.

**Mount in Pod:**

```yaml
volumes:
  - name: malware-samples
    persistentVolumeClaim: { claimName: malware-samples, readOnly: true }
volumeMounts:
  - { name: malware-samples, mountPath: /mnt/samples/malware, readOnly: true }
```

The Pod sees `/mnt/samples/malware/` structured identically to `/data/malware-samples/`. `config.data.dataset` in the rendered detector config points to `/mnt/samples` (parent of both subdirs).

**noexec:** hostPath mount doesn't expose a `noexec` flag. Adequate mitigation for Phase 4:

- Pod's `readOnlyRootFilesystem: true` prevents writing executables anywhere
- Containers run `runAsNonRoot: true` with `capabilities.drop: [ALL]` — no `exec` escalation possible
- NetworkPolicy deny-all-egress blocks any resulting process from reaching out
- Detector code reads sample bytes as feature input only; nothing in the maldet spec or upxelfdet invokes them as processes

Multi-node migration (future, one sudo session):

1. `sudo apt install nfs-kernel-server` on server30
2. Add `/data/malware-samples *(ro,no_subtree_check,sync)` to `/etc/exports`
3. `sudo exportfs -ra`
4. `helm install csi-driver-nfs csi-driver-nfs/csi-driver-nfs` (cluster install, no sudo)
5. Replace `malware-samples` PV definition: `hostPath` → `csi: { driver: nfs.csi.k8s.io, ... }`
6. PVC / Pod specs unchanged; rolling restart of any running jobs (acceptable — Phase 4 accepts job interruption during infra changes)

### Dataset Config (CSV manifest)

A **dataset config** is a reusable, versioned CSV manifest that lists which samples participate in a training/test/predict set. The CSV is **inline** in PostgreSQL — not referenced externally — so config content is immutable-by-reference once a job consumes it.

**CSV format requirements:**

- UTF-8, RFC 4180
- Required columns: `file_name`, `label`
- Optional columns: `family`, `md5`, `CPU`, `first_seen`, `size`, `is_packed`, any additional metadata (pass-through)
- `file_name` must be a SHA256 hex digest (64 lowercase hex chars)
- Platform validates each `file_name` exists in either `/mnt/samples/malware/{prefix}/{file_name}` or `/mnt/samples/benign/{prefix}/{file_name}` (checked at dataset config creation via backend-side lookup)

**Inline storage rationale:**

- Typical research subsets: 5k-50k samples → ~1-5 MB CSV (well under 10 MB cap)
- The master 2.15M-row catalog (300+ MB) is a one-off source; users filter it externally (pandas/DuckDB) before upload — the catalog itself is not a dataset config
- Keeps DB self-contained, simplifies backup, eliminates MinIO dependency for MVP

**Size cap: 10 MB** (configurable via `DATASET_CSV_MAX_BYTES` env). Exceeding this returns `413 Payload Too Large`. When a real need arises, add MinIO-backed storage as an optional path (YAGNI).

**Integrity:**

- On create: compute SHA256 over CSV bytes, store as `csv_checksum`
- On each job dispatch: re-verify checksum (CSV rows in DB haven't been tampered with) — cheap because it's bytes already loaded
- On each job dispatch: spot-check ≤ 100 random `file_name`s exist on disk (full scan is O(N) and too slow for 50k-row configs); incident-rate is low because hostPath is ro
- If ≥1 spot-check sample is missing → job rejected with `dataset_integrity_failed` error

---

## Data Model

Four new tables added, one existing Phase 3 table extended.

### `dataset_config`

```
id              UUID PK
name            String(100)               -- UI display name, unique per owner
description     Text nullable
owner_id        FK → user
visibility      Enum(public, private), default public
csv_content     Text                       -- full CSV bytes, UTF-8
csv_checksum    String(64)                 -- hex SHA256
sample_count    Int                        -- parsed row count (excl. header)
label_distribution JSONB                   -- {"Malware": 8500, "Benign": 1500}
family_distribution JSONB nullable         -- {"mirai": 420, "xorddos": 310, ...}
size_bytes      Int                        -- len(csv_content.encode("utf-8"))
created_at      Timestamp
deleted_at      Timestamp nullable         -- soft delete
```

**Unique constraint:** `(owner_id, name)` where `deleted_at IS NULL`.

### `job`

Unified table for all three job types; `type` column discriminates.

```
id                         UUID PK
type                       Enum(train, evaluate, predict)
status                     Enum(pending, preparing, running, succeeded, failed, cancelled, timeout)
detector_version_id        FK → detector_version
train_dataset_id           FK → dataset_config nullable   -- required for type=train
test_dataset_id            FK → dataset_config nullable   -- required for type=train, type=evaluate
predict_dataset_id         FK → dataset_config nullable   -- required for type=predict
source_model_version_id    FK → model_version nullable    -- required for type=evaluate, type=predict
owner_id                   FK → user
resolved_config            JSONB                          -- full config.json passed to detector
mlflow_experiment_id       String(50) nullable            -- set before Job launch
mlflow_run_id              String(50) nullable            -- set before Job launch
k8s_job_name               String(100) nullable
failure_reason             String(100) nullable           -- structured code
log_tail                   Text nullable                  -- last 8KB on finalize
summary_metrics            JSONB nullable                 -- snapshot for evaluate summary
resource_profile           Enum(standard)                 -- forward-compat: future "high_mem", "multi_gpu"
submitted_at               Timestamp
started_at                 Timestamp nullable
finished_at                Timestamp nullable
```

**Indexes:**

- `(owner_id, submitted_at DESC)` — user's job history
- `(status)` partial index `WHERE status IN ('pending','preparing','running')` — reconciler scan
- `(detector_version_id)` — "runs on this version"

**Job type → required refs matrix:**

| type     | train_dataset | test_dataset | predict_dataset | source_model |
| -------- | :-----------: | :----------: | :-------------: | :----------: |
| train    |  ✅ required  | ✅ required  |        —        |      —       |
| evaluate |       —       | ✅ required  |        —        | ✅ required  |
| predict  |       —       |      —       |   ✅ required   | ✅ required  |

The `test_dataset` for training is used by detectors like upxelfdet that split data for internal validation during training (maldet spec does not require it, but most detectors do; jobs without it are allowed but platform defaults to copying train→test in the rendered config if omitted).

### `model_version` (thin pointer to MLflow)

MLflow Model Registry is the source of truth. Lolday keeps a thin pointer table for fast listing + FK references.

```
id                UUID PK
mlflow_name       String(200)                -- e.g., "upxelfdet"
mlflow_version    Int                        -- MLflow's monotonic version
mlflow_run_id     String(50)                 -- originating run
current_stage     Enum(None, Staging, Production, Archived)
detector_version_id FK → detector_version    -- denormalized from mlflow tags
source_job_id     FK → job                   -- originating train job
owner_id          FK → user                  -- originating job's owner
created_at        Timestamp
last_transitioned_at Timestamp
```

**Unique constraint:** `(mlflow_name, mlflow_version)`.

Synced from MLflow via a periodic reconciler pass (same 10s loop); source-of-truth remains MLflow.

### `model_transition_log`

Audit trail for stage transitions (who, when, why).

```
id                  UUID PK
model_version_id    FK → model_version
from_stage          Enum(None, Staging, Production, Archived)
to_stage            Enum(None, Staging, Production, Archived)
actor_id            FK → user
comment             Text nullable
transitioned_at     Timestamp
```

### Extensions to Phase 3 `detector_version`

Add:

```
mlflow_experiment_id  String(50) nullable   -- 1 experiment per detector, created lazily
```

When the first job for a `detector_version` is submitted, backend creates (or looks up) a corresponding MLflow experiment named `detector:<detector.name>:<detector_version.git_tag>` and persists the experiment ID here. Subsequent jobs reuse it.

### Alembic Migration

One migration adds `dataset_config`, `job`, `model_version`, `model_transition_log`, plus the `mlflow_experiment_id` column on `detector_version`. Includes partial unique indexes where noted.

---

## API Endpoints

All prefixed `/api/v1`. Permissions use Phase 2's `require_role()` dep.

### Dataset Config

| Method | Path                   | Auth                               | Notes                                                                                                                               |
| ------ | ---------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/datasets`            | User+                              | body: `{name, description?, visibility?, csv_content}`; validates format, checksum, sample existence; returns 201 with parsed stats |
| GET    | `/datasets`            | User+                              | paginated; filters: `?owner_id=`, `?visibility=`, `?search=`                                                                        |
| GET    | `/datasets/{id}`       | User+ (owner or visibility=public) | full metadata including `label_distribution`; excludes `csv_content`                                                                |
| GET    | `/datasets/{id}/csv`   | User+ (owner or visibility=public) | raw CSV download with `Content-Disposition`                                                                                         |
| PATCH  | `/datasets/{id}`       | Owner/Admin                        | `name`, `description`, `visibility` only; content is immutable                                                                      |
| POST   | `/datasets/{id}/clone` | User+                              | duplicate content, caller becomes owner, `-clone` suffix on name                                                                    |
| DELETE | `/datasets/{id}`       | Owner/Admin                        | soft delete; rejected if any non-terminal job references it                                                                         |

### Jobs

| Method | Path                | Auth                          | Notes                                                                                                                                                                                              |
| ------ | ------------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/jobs`             | User+                         | body: `{type, detector_version_id, train_dataset_id?, test_dataset_id?, predict_dataset_id?, source_model_version_id?, params}`; validates matrix + params against schema; returns 202 with job_id |
| GET    | `/jobs`             | User+                         | paginated; filters: `?type=`, `?status=`, `?owner_id=`, `?detector_id=`, `?from=<ts>`                                                                                                              |
| GET    | `/jobs/{id}`        | User+ (owner or public model) | full detail: status, resolved_config, log_tail, mlflow_run_id, summary_metrics                                                                                                                     |
| GET    | `/jobs/{id}/logs`   | User+ (as above)              | live: proxy K8s API pod logs; after TTL (24h post-finalize): return `log_tail` + 410                                                                                                               |
| POST   | `/jobs/{id}/cancel` | Owner/Admin                   | deletes K8s Job; reconciler sets `cancelled`                                                                                                                                                       |

### MLflow Proxy (read-only)

Thin passthrough endpoints that attach user auth + visibility filtering. Backend is the only thing with MLflow credentials; users never hit MLflow directly in Phase 4. (MLflow own UI can be exposed in Phase 6 via tunnel + Cloudflare Access.)

| Method | Path                               | Auth  | Delegates to MLflow                      |
| ------ | ---------------------------------- | ----- | ---------------------------------------- |
| GET    | `/experiments`                     | User+ | `GET /api/2.0/mlflow/experiments/search` |
| GET    | `/experiments/{id}/runs`           | User+ | `POST /api/2.0/mlflow/runs/search`       |
| GET    | `/runs/{id}`                       | User+ | `GET /api/2.0/mlflow/runs/get`           |
| GET    | `/runs/{id}/artifacts`             | User+ | `GET /api/2.0/mlflow/artifacts/list`     |
| GET    | `/runs/{id}/artifacts/{path:path}` | User+ | download from MLflow artifact root       |

### Model Registry

| Method | Path                                     | Auth                       | Notes                                                                         |
| ------ | ---------------------------------------- | -------------------------- | ----------------------------------------------------------------------------- |
| GET    | `/models`                                | User+                      | paginated, filters: `?name=`, `?stage=`                                       |
| GET    | `/models/{name}`                         | User+                      | summary + latest per-stage versions                                           |
| GET    | `/models/{name}/versions`                | User+                      | all versions chronological                                                    |
| GET    | `/models/{name}/versions/{v}`            | User+                      | version detail (run_id, metrics, tags)                                        |
| POST   | `/models/{name}/versions/{v}/transition` | Developer (owner) or Admin | body: `{to_stage, comment?}`; writes `model_transition_log`; calls MLflow API |
| DELETE | `/models/{name}/versions/{v}`            | Owner / Admin              | only allowed on stage=`None` or `Archived`                                    |

---

## Job Submission & Lifecycle

### Submission flow (POST `/jobs`)

```
1. Authorize: require_role(User)
2. Load detector_version; reject if deleted
3. Load dataset refs per type matrix; reject if deleted or visibility denies
4. Load source_model_version (eval/predict); resolve MLflow run_id + artifact URI
5. Validate params against detector_version.config_schema (jsonschema, Draft 2020-12)
6. Compute idempotency key: sha256(user_id|detector_version_id|type|train_ds|test_ds|predict_ds|source_model|sorted_params)
   → if a job with same key submitted <5 min ago is non-terminal → 409 conflict
7. Check per-user in-flight cap: count non-terminal jobs for owner ≤ 2 → else 429
8. Re-verify dataset_config.csv_checksum and spot-check samples exist on disk
9. Create MLflow experiment (idempotent, per detector_version) → experiment_id
10. Create MLflow run (empty) → run_id
11. Insert job row: status=pending, mlflow_run_id set, resolved_config rendered
12. Launch K8s Job (see Job Pod spec below)
13. Update job: k8s_job_name set, status=preparing
14. Return 202 with {job_id, mlflow_run_id, status}
```

**Rendering `resolved_config`:**

The backend builds the detector's `config.json` by:

1. Start from detector_version's default config (derived from `config_schema`)
2. Merge user-supplied `params` (shallow-merged, with $schema-validated types)
3. Inject standardized paths:
   - `data.train` = `/mnt/config/train.csv` (if applicable)
   - `data.test` = `/mnt/config/test.csv`
   - `data.predict` = `/mnt/config/predict.csv`
   - `data.dataset` = `/mnt/samples`
   - `output.model` = `/mnt/output/model` (train) OR `/mnt/source-model` (eval/predict, read-only, populated by init container)
   - `output.feature` = `/mnt/output/features`
   - `output.vectorize` = `/mnt/output/vectorize`
   - `output.prediction` = `/mnt/output/prediction.csv`
   - `output.log` = `/mnt/output/logs`
4. Persist merged JSON to `job.resolved_config` (exact bytes the container will see)

### Reconciler loop (extended from Phase 3)

```python
async def job_reconciler():
    while not shutdown.is_set():
        in_flight = await db.get_jobs(status__in=["preparing", "running"])
        for j in in_flight:
            try:
                k8s_job = await k8s.read_job(j.k8s_job_name, namespace="lolday")
                pod = await k8s.get_job_pod(j.k8s_job_name)
                if pod and pod.status.phase == "Running" and j.status != "running":
                    await db.mark_running(j)
                elif k8s_job.status.succeeded:
                    await _finalize_success(j)
                elif k8s_job.status.failed:
                    reason = extract_failure_reason(pod)
                    log_tail = await k8s.read_pod_logs(pod, tail_bytes=8192)
                    await db.mark_failed(j, reason, log_tail)
                elif _activedeadline_exceeded(j, k8s_job):
                    await k8s.delete_job(j.k8s_job_name)
                    await db.mark_timeout(j)
            except Exception:
                logger.exception("reconcile failed", job_id=j.id)
        await _sync_model_versions()
        await asyncio.sleep(10)

async def _finalize_success(job):
    # Pull MLflow run metadata back as authoritative summary
    run = await mlflow.get_run(job.mlflow_run_id)
    summary = {
        "status": run.info.status,
        "metrics": run.data.metrics,
        "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
    }
    log_tail = await k8s.read_pod_logs(pod, tail_bytes=8192)
    # Auto-register trained model if train job
    if job.type == "train":
        await _register_model_from_run(run, job)
    await db.mark_succeeded(job, summary_metrics=summary["metrics"], log_tail=log_tail)
    await _emit_audit("job.finish", job_id=job.id, status="succeeded")
    # Phase 6: send completion email via Resend

async def _sync_model_versions():
    """Keep local model_version table in sync with MLflow Model Registry."""
    # MLflow REST: GET /api/2.0/mlflow/registered-models/search + /api/2.0/mlflow/model-versions/search
    # Upsert by (mlflow_name, mlflow_version); update current_stage if changed
    ...
```

### Failure modes & reasons

Structured failure codes (stored in `job.failure_reason`):

| Code                        | Origin                                |
| --------------------------- | ------------------------------------- |
| `dataset_integrity_failed`  | spot-check found missing sample       |
| `dataset_checksum_mismatch` | CSV content tampered post-create      |
| `params_schema_invalid`     | jsonschema validation failed          |
| `idempotency_duplicate`     | 5-min replay protection               |
| `concurrency_limit`         | per-user 2 in-flight cap              |
| `source_model_not_found`    | eval/predict references missing model |
| `detector_exit_nonzero`     | CLI returned ≠ 0                      |
| `detector_timeout`          | activeDeadlineSeconds exceeded        |
| `detector_oom`              | Pod OOMKilled (exit 137)              |
| `gpu_unavailable`           | no GPU satisfied request              |
| `mlflow_unreachable`        | MLflow server down during run         |
| `cancelled_by_user`         | POST /cancel                          |
| `cancelled_by_admin`        | admin override                        |

### Per-type `activeDeadlineSeconds`

- train: 6 h (21600) — typical upxelfdet SVM on 50k samples: 30 min; upper bound accommodates kernel trick variants
- evaluate: 30 min (1800)
- predict: 1 h (3600) — batches up to 100k samples

All tunable via `BUILD_CONCURRENCY_*` env vars. Jobs hitting the deadline are `timeout`-finalized with their pod logs preserved.

---

## Job Pod Specification

One container per job (detector image; no init container needed for train; init container for eval/predict to download source model).

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: job-train-{job_id_short}
  namespace: lolday
  labels:
    app.kubernetes.io/name: lolday-job
    lolday.job-id: "{job_id}"
    lolday.job-type: "train"
spec:
  activeDeadlineSeconds: 21600          # 6h for train
  ttlSecondsAfterFinished: 604800       # 7-day auto-cleanup
  backoffLimit: 0                        # no auto-retry
  template:
    metadata:
      labels:
        app.kubernetes.io/name: lolday-job
        lolday.job-id: "{job_id}"
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
        seccompProfile: { type: RuntimeDefault }

      nodeSelector:
        kubernetes.io/hostname: server30    # until multi-node

      volumes:
        - name: malware-samples
          persistentVolumeClaim: { claimName: malware-samples, readOnly: true }
        - name: benign-samples
          persistentVolumeClaim: { claimName: benign-samples, readOnly: true }
        - name: config
          emptyDir: { sizeLimit: 32Mi }
        - name: output
          emptyDir: { sizeLimit: 10Gi }
        - name: source-model       # only for eval/predict; empty otherwise
          emptyDir: { sizeLimit: 2Gi }
        - name: tmp
          emptyDir: { sizeLimit: 1Gi, medium: Memory }

      initContainers:
        # Writes config.json + train/test/predict CSVs to /mnt/config
        - name: config-writer
          image: harbor.harbor.svc:80/lolday/job-helper:v1
          command: [python, -m, job_helper.write_config]
          env:
            - { name: JOB_ID, value: "{job_id}" }
            - { name: BACKEND_URL, value: "http://backend.lolday.svc:8000" }
            - { name: JOB_TOKEN, valueFrom: { secretKeyRef: { name: job-token-{job_id}, key: token } } }
          volumeMounts:
            - { name: config, mountPath: /mnt/config }
          resources:
            limits: { cpu: 500m, memory: 256Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }

        # Only for eval/predict: downloads source model from MLflow to /mnt/source-model
        - name: model-fetcher
          image: harbor.harbor.svc:80/lolday/job-helper:v1
          command: [python, -m, job_helper.fetch_model]
          env:
            - { name: MLFLOW_TRACKING_URI, value: "http://mlflow.lolday.svc:5000" }
            - { name: SOURCE_RUN_ID, value: "{source_run_id}" }
            - { name: ARTIFACT_PATH, value: "model" }
          volumeMounts:
            - { name: source-model, mountPath: /mnt/source-model }
          resources:
            limits: { cpu: 500m, memory: 512Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }

      containers:
        - name: detector
          image: harbor.harbor.svc:80/detectors/{name}:{tag}
          # detector's Dockerfile ENTRYPOINT is the CLI (e.g., "upxelfdet");
          # args appended to it:
          args: [train, --config, /mnt/config/config.json]
          env:
            - { name: MLFLOW_TRACKING_URI, value: "http://mlflow.lolday.svc:5000" }
            - { name: MLFLOW_RUN_ID, value: "{mlflow_run_id}" }
            - { name: MLFLOW_EXPERIMENT_ID, value: "{mlflow_experiment_id}" }
            # ensure libs find writable tmp dirs
            - { name: TMPDIR, value: "/tmp" }
            - { name: HOME, value: "/tmp" }
          volumeMounts:
            - { name: config, mountPath: /mnt/config, readOnly: true }
            - { name: output, mountPath: /mnt/output }
            - { name: source-model, mountPath: /mnt/source-model, readOnly: true }
            - { name: malware-samples, mountPath: /mnt/samples/malware, readOnly: true }
            - { name: benign-samples, mountPath: /mnt/samples/benign, readOnly: true }
            - { name: tmp, mountPath: /tmp }
          resources:
            requests: { cpu: 2, memory: 4Gi }
            limits:
              cpu: 4
              memory: 16Gi
              nvidia.com/gpu: 1
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
```

**Notes:**

- `output` emptyDir is the handoff surface between detector and MLflow. Because maldet's CLI will `mlflow.log_artifacts()` from within the container, Phase 4 does not need a post-hook to scrape output — it's already uploaded before the Pod exits. The emptyDir is discarded.
- For resource-poor detectors (no GPU), a separate job template with `nvidia.com/gpu: 0` is supported via `resource_profile: cpu_only`. Phase 4 ships `standard` (1 GPU) only; `cpu_only` added when first non-GPU detector is registered (YAGNI).
- `tmp` is memory-backed (1 Gi) to accommodate sklearn's `joblib`, HuggingFace `transformers` caches, etc., without touching rootfs.

### Job-scoped one-time token

Analogous to Phase 3's `build_token`. Platform generates a UUID, stores hashed version in DB (column `job.token_hash`), injects raw token into `job-token-{job_id}` Secret. Secret is deleted in reconciler's finalize. Used by:

- `config-writer` init container (calls back to backend for `resolved_config` via `/internal/jobs/{id}/config`)

### NetworkPolicy (kube-router L3/L4)

NetworkPolicy applies to the whole Pod — init and main containers share the network namespace, so one policy covers both. Egress is the union of what the config-writer init needs (backend:8000) and what the detector main container may legitimately need (MLflow + DNS).

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: lolday-job-egress, namespace: lolday }
spec:
  podSelector: { matchLabels: { app.kubernetes.io/name: lolday-job } }
  policyTypes: [Ingress, Egress]
  ingress: [] # deny all ingress (jobs accept no connections)
  egress:
    # kube-dns
    - to:
        - namespaceSelector:
            { matchLabels: { kubernetes.io/metadata.name: kube-system } }
      ports:
        - { protocol: UDP, port: 53 }
        - { protocol: TCP, port: 53 }
    # MLflow (used by detector main container for tracking/artifact logging)
    - to:
        - namespaceSelector:
            { matchLabels: { kubernetes.io/metadata.name: lolday } }
          podSelector: { matchLabels: { app.kubernetes.io/component: mlflow } }
      ports:
        - { protocol: TCP, port: 5000 }
    # Backend (used by config-writer init container; see trade-off in A14)
    - to:
        - namespaceSelector:
            { matchLabels: { kubernetes.io/metadata.name: lolday } }
          podSelector: { matchLabels: { app.kubernetes.io/component: backend } }
      ports:
        - { protocol: TCP, port: 8000 }
```

Nothing else is reachable: no Harbor, no K8s API, no public internet, no samples NFS server (local hostPath), no PyPI — the detector image has everything pre-installed from Phase 3 build. See A14 for the backend-egress trade-off.

````

Main container attempts to reach backend:8000 would be *allowed* by this policy — accepted trade-off. Mitigation: backend's `/internal/jobs/{id}/config` endpoint requires the job token, and the token is only injected into the init container's env (not main). Main container cannot read init's env. A leaked token from init (e.g., via logs) would need further exploit; acceptable for Phase 4.

**Future tightening (Phase 6):** swap to Cilium with per-container policies, or inject token via file that init deletes before main starts (`postStart` hook).

---

## MLflow Deployment

### Helm setup

Community charts for MLflow are uneven; Phase 4 uses a **self-maintained Deployment + PVC** (small, 1 file) backed by an official MLflow image. The maintenance cost is a single Docker image tag pin + env; trade-off accepted because the alternative (`community-charts/mlflow`) drags in NGINX, separate postgres, and optional S3/MinIO that we don't want.

```yaml
# charts/lolday/templates/mlflow.yaml (summary)

apiVersion: apps/v1
kind: Deployment
metadata: { name: mlflow, namespace: lolday }
spec:
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/component: mlflow } }
  template:
    metadata: { labels: { app.kubernetes.io/component: mlflow, app.kubernetes.io/name: mlflow } }
    spec:
      securityContext: { runAsNonRoot: true, runAsUser: 1000, fsGroup: 1000 }
      containers:
        - name: mlflow
          image: ghcr.io/mlflow/mlflow:v2.20.3
          command: [mlflow, server]
          args:
            - --host=0.0.0.0
            - --port=5000
            - --backend-store-uri=postgresql+psycopg2://$(PG_USER):$(PG_PASSWORD)@postgresql.lolday.svc:5432/mlflow
            - --default-artifact-root=/mlflow-artifacts
            - --serve-artifacts
          env:
            - { name: PG_USER,     valueFrom: { secretKeyRef: { name: mlflow-db, key: username } } }
            - { name: PG_PASSWORD, valueFrom: { secretKeyRef: { name: mlflow-db, key: password } } }
          ports: [{ containerPort: 5000 }]
          volumeMounts:
            - { name: artifacts, mountPath: /mlflow-artifacts }
          resources:
            requests: { cpu: 200m, memory: 512Mi }
            limits:   { cpu: 2, memory: 4Gi }
          readinessProbe: { httpGet: { path: /health, port: 5000 }, initialDelaySeconds: 10 }
          livenessProbe:  { httpGet: { path: /health, port: 5000 }, initialDelaySeconds: 30 }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
      volumes:
        - name: artifacts
          persistentVolumeClaim: { claimName: mlflow-artifacts }

---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: mlflow-artifacts, namespace: lolday }
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 100Gi } }   # bump as needed; main constraint: one 2 GB/model × 1000 models ≈ 2 TB worst-case — 100Gi enough for MVP
  storageClassName: local-path
---
apiVersion: v1
kind: Service
metadata: { name: mlflow, namespace: lolday }
spec:
  selector: { app.kubernetes.io/component: mlflow }
  ports: [{ port: 5000, targetPort: 5000 }]
````

### PostgreSQL schema setup

Reuse the existing `postgresql` StatefulSet (Phase 2). Create a separate **database** (not schema) called `mlflow` with its own user, to isolate MLflow's DDL from lolday's. Achieved via a post-install Helm hook job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: mlflow-db-init
  namespace: lolday
  annotations:
    {
      helm.sh/hook: post-install,
      post-upgrade,
      helm.sh/hook-weight: "5",
      helm.sh/hook-delete-policy: hook-succeeded,
    }
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: init
          image: postgres:16
          command: [sh, -c]
          args:
            - |
              export PGPASSWORD="$PG_ADMIN_PASSWORD"
              psql -h postgresql.lolday.svc -U postgres <<SQL
              SELECT 'CREATE DATABASE mlflow' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\\gexec
              DO \$\$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mlflow') THEN
                  CREATE USER mlflow WITH PASSWORD '$PG_MLFLOW_PASSWORD';
                END IF;
              END \$\$;
              GRANT ALL PRIVILEGES ON DATABASE mlflow TO mlflow;
              SQL
          env:
            - {
                name: PG_ADMIN_PASSWORD,
                valueFrom:
                  {
                    secretKeyRef:
                      { name: lolday-postgresql, key: postgres-password },
                  },
              }
            - {
                name: PG_MLFLOW_PASSWORD,
                valueFrom: { secretKeyRef: { name: mlflow-db, key: password } },
              }
```

MLflow server auto-creates its own tables on first run.

### Access paths

| Source                         | Path                                                                                  |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| Backend → MLflow               | `http://mlflow.lolday.svc:5000` (direct)                                              |
| Job pod → MLflow               | `http://mlflow.lolday.svc:5000` (allowed by NetworkPolicy)                            |
| Admin (dev laptop) → MLflow UI | `kubectl port-forward svc/mlflow 5000:5000` for Phase 4; Cloudflare Tunnel in Phase 6 |

### Artifact storage layout

Default artifact root `/mlflow-artifacts` on PV. MLflow creates per-experiment/per-run subdirs automatically. Backup strategy (Phase 6) rsyncs this PV to Cloudflare R2.

---

## maldet Framework Changes (upstream PR)

User is the author of `islab-malware-detector`. Phase 4 lands a PR to it with these changes:

### Dependency addition

```toml
[project.optional-dependencies]
mlflow = ["mlflow>=2.20.0"]
```

Detector repos bump their maldet constraint to `islab-malware-detector[mlflow]>=0.5.0` (or Phase 3 already pins `>=0.4.0` → new major bump).

### CLI wrapping (in `maldet/cli.py`)

Refactor `build_cli` so each command optionally wraps its body in an MLflow run context. Pseudocode:

```python
# maldet/cli.py (new imports + helpers + command refactor)

import os, json
from pathlib import Path

def _mlflow_enabled() -> bool:
    return bool(os.getenv("MLFLOW_TRACKING_URI"))

def _flatten_config(cfg) -> dict:
    """Collapse Pydantic config into MLflow-friendly flat params."""
    d = cfg.model_dump(mode="json")
    out = {}
    def walk(prefix, v):
        if isinstance(v, dict):
            for k, vv in v.items():
                walk(f"{prefix}.{k}" if prefix else k, vv)
        elif isinstance(v, (list, tuple)):
            out[prefix] = json.dumps(v)
        else:
            out[prefix] = v
    walk("", d)
    return out

def _maybe_mlflow_run():
    """Context manager factory: returns an active run if tracking enabled, else nullcontext."""
    from contextlib import nullcontext
    if not _mlflow_enabled():
        return nullcontext()
    import mlflow
    run_id = os.getenv("MLFLOW_RUN_ID")
    return mlflow.start_run(run_id=run_id)

def _log_common(cfg, action):
    if not _mlflow_enabled():
        return
    import mlflow
    mlflow.set_tag("maldet.action", action)
    mlflow.log_dict(cfg.model_dump(mode="json"), "config.json")
    for k, v in _flatten_config(cfg).items():
        # MLflow param values capped at 500 chars; truncate safely
        mlflow.log_param(k[:250], str(v)[:500])
    # Autolog sklearn/xgboost/etc. — silent, non-fatal if framework absent
    try:
        mlflow.autolog(log_models=False, silent=True)  # we handle model logging manually
    except Exception:
        pass

@app.command()
def train(config=None, log_level="INFO", log_format="console"):
    configure_logging(level=log_level, format=log_format)
    cfg = config_class.from_file(config) if config else config_class()
    with _maybe_mlflow_run():
        _log_common(cfg, "train")
        detector = detector_class(cfg)
        model_path = detector.train()
        if _mlflow_enabled():
            import mlflow
            if model_path and Path(model_path).exists():
                mlflow.log_artifacts(str(model_path), artifact_path="model")
            # Register in Model Registry with pending stage=None
            model_name = os.getenv("MLFLOW_MODEL_NAME", detector_class.__name__)
            mlflow.register_model(f"runs:/{mlflow.active_run().info.run_id}/model", model_name)
    typer.echo(f"Model saved to {model_path}")

@app.command()
def evaluate(config=None, log_level="INFO", log_format="console"):
    configure_logging(level=log_level, format=log_format)
    cfg = config_class.from_file(config) if config else config_class()
    with _maybe_mlflow_run():
        _log_common(cfg, "evaluate")
        detector = detector_class(cfg)
        metrics = detector.evaluate()

        # Always write metrics.json (platform-side tooling tolerates no-MLflow environments)
        log_dir = Path(cfg.output.log)
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "metrics.json").write_text(
            json.dumps(metrics, default=str, indent=2)
        )

        if _mlflow_enabled():
            import mlflow
            # MLflow metrics must be numeric; coerce, skip non-numeric
            numeric = {}
            for k, v in metrics.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric[k] = float(v)
            mlflow.log_metrics(numeric)
            mlflow.log_artifact(str(log_dir / "metrics.json"))
    typer.echo("Evaluation Results:")
    for k, v in metrics.items():
        typer.echo(f"  {k}: {v}")

@app.command()
def predict(config=None, log_level="INFO", log_format="console"):
    configure_logging(level=log_level, format=log_format)
    cfg = config_class.from_file(config) if config else config_class()
    with _maybe_mlflow_run():
        _log_common(cfg, "predict")
        detector = detector_class(cfg)
        output_path = detector.predict()
        if _mlflow_enabled() and output_path and Path(output_path).exists():
            import mlflow
            mlflow.log_artifact(str(output_path), artifact_path="prediction")
    typer.echo(f"Predictions saved to {output_path}")
```

### Backwards compatibility

- No `MLFLOW_TRACKING_URI` env → original behavior (metrics.json is the only net-new side effect, and it's additive)
- `mlflow` is an optional extra: detectors that don't opt in still work
- Detector code (`train/evaluate/predict` methods) is unchanged — the wrapping is entirely in maldet's CLI

### upxelfdet changes

- Bump `islab-malware-detector` dep: `islab-malware-detector[mlflow]>=0.5.0`
- No code change required
- Dockerfile: extras are propagated via pip install

### Testing maldet changes

- Unit test with MLflow tracking URI unset → current behavior
- Unit test with `MLFLOW_TRACKING_URI=sqlite:///tmp.db` → verify metrics/artifacts logged
- Integration: run upxelfdet against tiny fixture dataset with mocked MLflow

---

## Job Helper Image

`charts/lolday/helpers/job-helper/` — small platform-side image similar to Phase 3's `build-helper`, pushed to `harbor.harbor.svc:80/lolday/job-helper:v1`.

Contents:

- `job_helper/write_config.py` — fetches `/internal/jobs/{id}/config` with job token, writes `config.json` + dataset CSVs to `/mnt/config/`
- `job_helper/fetch_model.py` — downloads artifacts from MLflow run's `model/` path to `/mnt/source-model/`
- Base: `python:3.12-slim` + `httpx` + `mlflow` (only client, not server)
- Built once, bumped on helper changes (version tag baked into Helm values)

The helper is authored, tested, and released together with the backend; it's not a detector.

### Internal endpoint for config

```
POST /api/v1/internal/jobs/{id}/config
Auth: job-token (single-use, stored hashed in job.token_hash)
Returns: {
  "config": { ... resolved_config ... },
  "train_csv": "<csv bytes>" | null,
  "test_csv": "<csv bytes>" | null,
  "predict_csv": "<csv bytes>" | null
}
```

Helper writes these to `/mnt/config/config.json`, `/mnt/config/train.csv`, etc. Token consumed once (marked used in DB); reconciler clears Secret after job finalize.

---

## Model Registry UX

MLflow Model Registry is the source of truth. Lolday wraps it for RBAC + audit.

### Registration on train success

When a train job succeeds:

1. Reconciler's `_register_model_from_run` calls MLflow: `create_model_version(name=<detector.name>, source=runs:/<run>/model)`
2. Initial stage is `None` (MLflow default)
3. Sync creates local `model_version` row with `current_stage=None`, `detector_version_id`, `source_job_id`, `owner_id`
4. Model name convention: `<detector.name>` (e.g., `upxelfdet`) — one registered model per detector, N versions per detector (version = MLflow-assigned, incrementing)

### Stage transitions

`POST /models/{name}/versions/{v}/transition` with body `{to_stage: "Production"|"Staging"|"Archived", comment?: "..."}`:

1. Load `model_version` row; reject if missing
2. Permission check:
   - Admin: any transition
   - Developer who owns originating job: any transition for their model
   - User: denied
3. Validate transition: MLflow allows any→any; lolday enforces `None → Staging → Production → Archived` forward + allow rollback `Production → Archived`, `Archived → None` (re-activate) by Admin only
4. Call MLflow: `transition_model_version_stage(name, version, stage, archive_existing_versions=true if stage==Production else false)`
5. MLflow's `archive_existing_versions=true` auto-moves old Production → Archived (ensures single Production version)
6. Upsert `model_version.current_stage`; insert `model_transition_log` row
7. Emit audit event

### Visibility

- Production models: always visible (cannot be made private)
- Staging / Archived / None: visible to owner + Admin; others see in list only if `include_private=true` param and they're Admin
- Enforced in `/models*` list/detail endpoints

---

## Security Summary

| Threat                                   | Control                                                                                                                                                                     |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Malware sample executed inside job pod   | `readOnlyRootFilesystem: true` + no-exec via `runAsNonRoot` + capabilities dropped — executing a file requires copying it to a writable path with exec bit, both disallowed |
| Data exfiltration via training pod       | Deny-all egress except DNS + MLflow + backend; no Harbor, no internet, no K8s API; SA token not mounted                                                                     |
| Malware corrupts adjacent jobs           | Each job gets its own Pod; emptyDir volumes isolated; hostPath sample mounts are ReadOnly                                                                                   |
| Malware tampers with MLflow              | MLflow credentials limited per-run (run_id scoped); artifact writes go to run-specific subdir; no admin API exposed to job pods                                             |
| Job token leak                           | One-time token, hashed in DB, injected only into config-writer init container, Secret deleted on job finalize, 1-hour TTL at creation                                       |
| Dataset integrity bypass                 | SHA256 checksum re-verified at dispatch; spot-check sample existence; CSV content immutable (PATCH endpoint excludes csv_content)                                           |
| Promotion to Production by non-owner     | `POST /models/.../transition` enforces owner-or-admin; audit log records actor                                                                                              |
| Concurrency abuse (resource exhaustion)  | Per-user 2 in-flight cap; global K8s scheduler naturally queues pending GPU-requesting pods                                                                                 |
| SSH interruption                         | No CNI changes; no host-level changes during Phase 4 deploy; MLflow ClusterIP only; PV uses existing local-path provisioner                                                 |
| Phase 3 build vs Phase 4 job interaction | Both share `lolday` namespace but different Role bindings not needed — backend SA already has scoped perms; jobs have no SA token; NetworkPolicies separate by label        |

### Audit log

stdout (Phase 6 Loki captures):

```
AUDIT dataset.create       user=<id> dataset=<id> samples=<n> visibility=<v>
AUDIT dataset.delete       user=<id> dataset=<id>
AUDIT job.submit           user=<id> job=<id> type=<t> detector=<id> datasets=[...]
AUDIT job.finish           job=<id> status=<s> duration_s=<n>
AUDIT job.cancel           user=<id> job=<id> reason=<r>
AUDIT model.transition     user=<id> model=<name> version=<v> from=<s1> to=<s2>
```

---

## Testing Strategy

### Unit (pytest, no K8s, no MLflow)

| Module                          | Focus                                                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `services/dataset.py`           | CSV parse + checksum + label distribution; reject malformed; reject non-SHA256 names; reject oversized |
| `services/job.py`               | Config rendering: per-type path injection, schema validation, idempotency key                          |
| `services/mlflow_client.py`     | MLflow REST calls with `respx` mocks; retry on 503; error mapping                                      |
| `services/k8s.py`               | Job spec generation: correct volumes/env/security for each type; GPU request inclusion                 |
| `reconciler.py`                 | State transitions: preparing→running, success/failure/timeout; model-version sync                      |
| `services/dataset_integrity.py` | Spot-check logic: skip missing ≤ threshold; fail on ≥ threshold                                        |

### Integration (FastAPI TestClient + aiosqlite + mocked K8s + mocked MLflow)

- Submit job → reconciler transitions to running → succeeded
- Submit with bad params → 422 with schema errors
- Dataset deletion blocked while jobs reference it
- Clone dataset → new row, same content, new owner
- Model transition → lolday row updated, audit log written
- Idempotency: duplicate POST within 5 min → 409

### E2E (manual, on server30)

Per-release checklist, committed as `docs/phase4-e2e-checklist.md`:

1. Upload dataset config (subset of Malware202403_info.csv, e.g., 5k samples)
2. Submit `train` job for upxelfdet v0.4.0 using that dataset
3. Watch job transition: pending → preparing → running → succeeded
4. Verify MLflow run has: config.json, model/ artifacts, flat params, sklearn autolog metrics
5. Verify `model_version` row created with stage=None
6. Submit `evaluate` job using test dataset + version=1 → check metrics in MLflow + `summary_metrics` in DB
7. Submit `predict` job using predict dataset + version=1 → check prediction.csv artifact
8. Promote version=1 to Production → verify `model_transition_log` + MLflow stage
9. Cancel a mid-flight job → verify cleanup

### Smoke test (deploy-time)

- MLflow server reachable from backend Pod (`curl http://mlflow.lolday.svc:5000/health`)
- Create dummy MLflow experiment via REST
- Launch minimal "sleep 5" detector-mock Job → confirm reconciler tracks it to success

---

## Helm / Deployment

### New / changed files

```
charts/lolday/
├── Chart.yaml                             # no new sub-chart (MLflow inline)
├── values.yaml                            # + mlflow.*, + job.*, + samples.*
├── templates/
│   ├── mlflow.yaml                         # NEW: Deployment + PVC + Service
│   ├── mlflow-db-init-job.yaml             # NEW: post-install hook
│   ├── mlflow-secret.yaml                  # NEW: DB user password from --set
│   ├── samples-pv.yaml                     # NEW: malware + benign PVs (hostPath)
│   ├── samples-pvc.yaml                    # NEW: matching PVCs
│   ├── job-networkpolicy.yaml              # NEW: strict egress for lolday-job
│   ├── job-helper-image-build-note.md      # NEW: doc stub for image build flow
│   ├── backend-rbac.yaml                   # EXTEND: add models/gets for jobs label selector
│   └── backend.yaml                        # EXTEND: add MLFLOW_TRACKING_URI env
├── helpers/
│   ├── build-helper/                       # existing
│   └── job-helper/                         # NEW: Dockerfile + job_helper/*.py
```

### values.yaml additions

```yaml
mlflow:
  enabled: true
  image: ghcr.io/mlflow/mlflow:v2.20.3
  storage: 100Gi
  secrets:
    dbPassword: "" # --set

samples:
  malware:
    enabled: true
    hostPath: /data/malware-samples
    storage: 500Gi
  benign:
    enabled: true
    hostPath: /data/benign-samples
    storage: 100Gi

jobs:
  helperImage: harbor.harbor.svc:80/lolday/job-helper:v1
  activeDeadlineSeconds:
    train: 21600
    evaluate: 1800
    predict: 3600
  perUserConcurrency: 2
  idempotencyWindowSeconds: 300

backend:
  env:
    MLFLOW_TRACKING_URI: http://mlflow.lolday.svc:5000
    DATASET_CSV_MAX_BYTES: "10485760" # 10 MiB
```

### Deploy steps (extend `scripts/deploy.sh`)

Add after existing Phase 3 Harbor setup:

1. **Prepare sample directories (one-time, user runs manually):**
   - `sudo mkdir -p /data/malware-samples /data/benign-samples`
   - `sudo chown bolin8017:bolin8017 /data/malware-samples /data/benign-samples` (owner matches K3s runtime)
   - `sudo chmod 755 /data/*`
   - (Populate with samples externally)
   - _Not in `deploy.sh`_ — prompt user in README; this is pre-infrastructure

2. **Build + push job-helper image:**
   - `docker build -t harbor.harbor.svc:80/lolday/job-helper:v1 charts/lolday/helpers/job-helper/`
   - `docker push harbor.harbor.svc:80/lolday/job-helper:v1`

3. **Helm upgrade:**
   - `helm upgrade --install lolday ... --set mlflow.secrets.dbPassword=<generated>`
   - Post-install hook runs `mlflow-db-init-job` → creates `mlflow` DB
   - MLflow server auto-creates tables on first start
   - Backend picks up new env vars

4. **Post-deploy smoke:**
   - `kubectl -n lolday wait deploy/mlflow --for=condition=Available --timeout=120s`
   - `kubectl -n lolday exec deploy/backend -- curl -sf http://mlflow.lolday.svc:5000/health`

### Rollback

- MLflow failure → `helm rollback lolday`; MLflow DB retained (just reattach on next attempt)
- Sample PV broken → PV can be deleted + recreated; data on hostPath not affected
- Bad job reconciler → `kubectl scale deploy/backend --replicas=0 && fix && scale up`; in-flight Jobs continue, get reconciled when backend returns

---

## Decisions & Amendments

Decisions made during Phase 4 design. Supersedes main spec where noted.

### A8. MLflow hosted inline (not as sub-chart)

**Main spec §2:** lists MLflow but doesn't specify deployment.

**Amendment:** Phase 4 writes a minimal inline Deployment + PVC for MLflow, using the official `ghcr.io/mlflow/mlflow` image directly. No community Helm sub-chart.

**Why:** community MLflow charts bundle NGINX, separate Postgres, S3/MinIO defaults that we don't want. Our needs are thin (1 replica, existing Postgres, PV artifacts, no ingress until Phase 6). A 50-line template is simpler and more auditable than customizing a sub-chart.

### A9. No Volcano; no Celery; no Kueue

**Main spec §2:** lists Volcano + Celery.

**Amendment:** Phase 4 does not deploy any batch scheduler / worker framework. Jobs are K8s `Job` objects tracked by the Phase 3 asyncio reconciler.

**Why:** 2-GPU lab with expected ≤10 concurrent users has queue depth of maybe 3-5 at peak. Per-user cap (2 in-flight) + K8s scheduler natural queueing covers this. Volcano's gang scheduling / fair-share is overkill. Celery adds a second state store (Redis broker) whose consistency with K8s Job state is non-trivial. Kueue is a reasonable middle ground — revisit in a future phase if multi-GPU distributed training becomes a real need.

### A10. maldet framework is modified (not wrapped)

**New decision:** MLflow tracking is added to `islab-malware-detector`'s CLI layer, not as a platform-side sidecar or subprocess wrapper.

**Why:** User owns maldet; the CLI contract is already the detector execution interface; adding `MLFLOW_TRACKING_URI`-gated tracking costs ~100 lines in one repo and propagates to every detector automatically. The alternative (platform-side wrapper) requires subprocess orchestration, metrics scraping from stdout, and artifact collection via post-job container — all fragile and per-detector-specific. maldet changes are backwards-compatible (tracking is env-gated).

**Trade-off:** maldet and lolday now share an invariant (env var contract). Documented in maldet's README; broken contract would be caught by Phase 4 E2E.

### A11. Dataset CSV inline in DB, no MinIO for MVP

**Main spec §5.2:** says `csv_content text`.

**Amendment confirmed + sized:** inline up to 10 MB; no MinIO / no external blob storage in Phase 4.

**Why:** Typical research subsets fit comfortably; master catalog (2.15M rows, 300+ MB) is a one-off source and not itself a dataset config. Adding MinIO means another StatefulSet + Helm sub-chart + backup target to maintain; YAGNI until someone actually needs > 10 MB.

### A12. Sample storage via hostPath PV, not NFS in Phase 4

**Main spec §5.1:** says NFS CSI.

**Amendment:** Phase 4 uses hostPath PV on server30. NFS CSI migration path documented but not executed.

**Why:**

- Single node; hostPath is faster (no network stack) and has zero new dependencies
- NFS installation requires sudo (user doesn't have it routinely)
- PV/PVC abstraction means switching to NFS is a 1-file change later
- For 1M files at 300-400 GB, hostPath read-only is performant; 2 concurrent GPU jobs = trivial read concurrency

### A13. Model Registry = MLflow built-in (thin lolday pointer)

**Main spec §7.2:** describes stages Staging/Production/Archived.

**Decision:** Use MLflow's Model Registry primitives directly. lolday maintains a `model_version` pointer table (for FK references + fast listing + owner attribution), but MLflow remains the source of truth for stages.

**Why:** MLflow has mature Model Registry with REST API, stage archival, and auto-promotion logic. Reimplementing this in lolday violates the "no custom code where an open-source tool exists" principle. The lolday pointer table is a ~200-line addition (model + sync loop + audit log) vs. writing a full registry.

### A14. NetworkPolicy allows job → backend:8000 (accepted risk)

**Concern:** Strict policy would disallow backend access from jobs, but init container needs it to fetch config.

**Decision:** Single Pod-level policy allows backend + MLflow + DNS egress. Backend access is protected by one-time job token, not by network isolation. Init container and main container share network namespace.

**Trade-off:** A compromised detector could attempt to abuse the job token before it's consumed. Mitigations: (a) token is one-time and consumed within seconds of init start, (b) backend validates token→job pairing, (c) main container isn't given the token. Phase 6 Cilium (or per-container sidecar pattern) can tighten.

### A15. `config.data.dataset` mount path convention

**New decision:** All jobs mount samples at `/mnt/samples/{malware|benign}/{prefix}/{sha256}`, config points `data.dataset = /mnt/samples`.

**Why:** Detectors like upxelfdet walk `{data.dataset}/{prefix}/{filename}` internally. Matches the natural folder layout. Single convention across all detectors; detector code unchanged.

### A16. Dataset spot-check vs full verification

**Decision:** At job dispatch, spot-check ≤100 random samples (configurable) rather than full existence scan.

**Why:** 50k-sample config × `stat()` per file = ~10s even on SSD. 100 random samples catches catastrophic failures (folder unmounted, wrong path) without stalling submission. Incident rate is low: hostPath is RO from platform's view, and the curator process is the only writer.

---

## Phase Roadmap Touchpoints

| Phase | Name                      | Status      | Phase 4 impact                                                                                                                                                                                                                                                       |
| ----- | ------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | Infrastructure Foundation | ✅ Complete | No change                                                                                                                                                                                                                                                            |
| 2     | Backend Core              | ✅ Complete | No change (models.py / schemas.py already split)                                                                                                                                                                                                                     |
| 3     | Detector Lifecycle        | ✅ Complete | Reuses `detector_version` schema; adds `mlflow_experiment_id` column via migration                                                                                                                                                                                   |
| 4     | Dataset & Jobs            | **Current** | —                                                                                                                                                                                                                                                                    |
| 5     | Frontend                  | Pending     | Renders `detector_version.config_schema` as forms; displays `job.status`, streams logs, links to MLflow UI; Model Registry UI over `/models` endpoints                                                                                                               |
| 6     | Operations                | Pending     | Cloudflared Tunnel exposes MLflow UI (behind Access); Resend email on job finish (hook already in reconciler); Loki replaces `log_tail` + K8s log proxy; R2 backup of MLflow PV + `mlflow` DB; optional NFS CSI migration; optional MinIO for oversized dataset CSVs |

---

## Open Questions (to resolve during implementation)

1. **Detector CLI discovery:** Phase 3 stored `package_name` in detector table. Does it also store the `[project.scripts]` script name (i.e., CLI entrypoint)? If not, Phase 4 build pipeline needs to extract it or we rely on the detector Dockerfile's `ENTRYPOINT` being set correctly. Resolution: verify Phase 3 stores this; if not, add a migration + validator step.
2. **Benign samples source:** The user only described malware-samples folder layout. Benign samples path and format? Current assumption: same layout at `/data/benign-samples/`. Confirm during implementation.
3. **Detector test data convention:** upxelfdet requires a `test` CSV during training for internal validation. Does every maldet detector? If not, the `test_dataset` requirement for `type=train` should be downgraded to optional. Resolution: verify against maldet `BaseDetector` API and add conditional rendering if needed.
4. **GPU model selection:** With 2 identical RTX 2080 Ti cards, no selection needed. If the lab adds different GPUs later, we'll need a `gpu_type` column on job — add then.
