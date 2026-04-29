# Phase 8 — First real end-to-end usage: UX findings

**Date:** 2026-04-21
**Operator:** PO-LIN — first actual hands-on end-to-end usage of lolday,
not an automated regression test.
**Scope:** Two new detector templates (`elfrfdet`, `elfcnndet`), the
`gpu2` ResourceProfile, the 500M+500B dataset, and every job lifecycle
(train, evaluate, predict).
**Result:** RF path works end-to-end (F1 = 0.994). DL path cleanly builds
after the Phase 8.1 follow-up commits (auto-Trivy + ephemeral-storage +
TTL + validator redesign); the v0.1.1 CNN image itself is correctly
CVE-blocked by the platform security policy (1 Critical from the torch
wheel set), which is **expected** behaviour rather than a bug. A clean
CUDA base image would let the DL job proceed to 2-GPU scheduling
verification. 8 distinct UX / platform gaps surfaced, **5 fixed in this
PR** (original 4 + validator redesign), 2 documented as deferred, 1
reclassified as data-quality rather than bug.

Severity: **S1** = blocking, **S2** = rough, **S3** = nit.

---

## Summary table

| #   | Finding                                                                         | Sev | Fixed here                                                    | Follow-up                                                               |
| --- | ------------------------------------------------------------------------------- | --- | ------------------------------------------------------------- | ----------------------------------------------------------------------- |
| 1   | `GET /api/v1/builds/<id>` does not exist — path is nested under detector        | S3  | **✅ flat alias**                                             | —                                                                       |
| 2   | Harbor Trivy scan never auto-triggers; builds sit at `scanning` forever         | S1  | **✅ reconciler auto-triggers**                               | —                                                                       |
| 3   | Build validator `/tmp` EmptyDir 512Mi → any torch detector evicted              | S1  | **✅ validator redesign (AST + --no-deps)**                   | —                                                                       |
| 4   | Validator RSS 2Gi → OOM on torch install                                        | S1  | **✅ same as #3**                                             | —                                                                       |
| 5   | Kaniko container RSS 4Gi → DL snapshot OOMs                                     | S1  | **✅ 20Gi**                                                   | Switch to BuildKit long-term                                            |
| 6   | Kaniko triggers **node-level ephemeral-storage eviction** building DL detectors | S1  | **✅ ephemeral-storage req/limit + 1h TTL for failed builds** | —                                                                       |
| 7   | 19 % of samples have no `.text` section / fail to parse                         | S2  | **✅ elfrfdet+elfcnndet catch ELFError**                      | Revisit dataset curation (statically linked Go / heavily stripped ELFs) |
| 8   | Build concurrency-limit 429 does not state the limit                            | S3  | **✅ includes `limit` + `in_flight`**                         | —                                                                       |
| 9   | DL detector image has 1 Critical + 9 High CVE from torch transitive deps        | N/A | — (expected)                                                  | Detector author picks a CVE-clean CUDA base image                       |

---

## What works after this PR

1. **ResourceProfile.GPU2 flows end-to-end through the API.** Verified
   via `POST /api/v1/jobs` body `{"resource_profile":"gpu2"}`: accepted
   (202), rejected (`gpu99` → 422). Enum available in the DB after
   Alembic migration applies on `helm upgrade`.
2. **`elfrfdet` template end-to-end**:
   - Register → build v0.1.1 → Trivy scanned → active version → train
     job (standard profile) → model registered in MLflow → evaluate
     (F1 = **0.994** / acc = **0.994** / precision = **1.000** /
     recall = **0.987** on 168 test samples) → predict (generates
     `predictions.csv`). Wall time for train: 10 seconds.
3. **Dataset sampling script** (`scripts/sample_elf_dataset.py`) built a
   balanced 500M+500B stratified 80/20 dataset from local manifests + the
   on-disk samples in `/data/samples`.

## What is still broken — DL path

`elfcnndet` (PyTorch 1D-CNN with `nn.DataParallel` for the gpu2 profile)
**could not be built** on this cluster even after three rounds of resource
bumping. The detector code itself works (8/8 unit tests pass on the dev
host including the multi-GPU branch, exercised via mocked
`torch.cuda.device_count`), but kaniko running inside a Volcano Job on
server30 hits these in sequence:

1. Validate container `/tmp` overflows at 512Mi → 12Gi.
2. Validate container RSS OOMs at 2Gi → 8Gi.
3. Kaniko snapshot OOMs at 4Gi → 12Gi → 20Gi.
4. Finally: **node-level ephemeral-storage eviction at 3Gi threshold** —
   kaniko had no `ephemeral-storage` resource request, making it the
   first-to-evict on a noisy node. 5 failed build pods kept their 14Gi
   EmptyDir volumes on disk until manually deleted.

Root cause is that `maldet_validator.py` performs a full `uv pip install`
of the detector repo just to extract a JSON schema. For torch detectors
this pulls ~7Gi of nvidia-cu12 wheels (cudnn, cublas, cufft, cusolver,
cusparse, nccl, cuda-nvrtc) into `/tmp` and kaniko then has to snapshot
that entire filesystem. The build pipeline was only sized for
sklearn-class deps.

### Follow-up now landed (Phase 8.1, commit d2b5462)

- **ephemeral-storage req/limit on validate + kaniko containers** — see
  `backend/app/services/build.py`. Rows 3 / 5 / 6 in the summary table.
- **Validator redesigned to --no-deps + AST discovery** — see
  `charts/lolday/helpers/build-helper/maldet_validator.py`. Convention
  published: `config.py` must only import `maldet` + `pydantic` +
  `pydantic-settings` + pure-python siblings.

### Still deferred

- **Replace Kaniko with BuildKit rootless** — long-term architectural
  change. Kaniko's full-filesystem snapshot model is a poor fit for
  DL-scale dependency footprints even after the validator redesign,
  because the final image layer (detector + base) still needs to be
  snapshotted. BuildKit's layer-level diff is fundamentally more
  memory-efficient. Not blocking Phase 8.

## Fixed in this PR

Platform (`backend/app/services/build.py`):

- Validate `/tmp` EmptyDir: 512Mi → 12Gi
- Validate RSS limit: 2Gi → 8Gi
- Kaniko RSS limit: 4Gi → 20Gi

Detector (`bolin8017/elfrfdet`, `bolin8017/elfcnndet`):

- v0.1.1 tag on both — catches the full `ELFError` hierarchy so a
  single malformed sample can't crash the feature pass. (pyelftools'
  `get_section_by_name` parses the section-header string table
  lazily and raises `ELFParseError` after the `ELFFile(f)` constructor
  has already returned.)

## Raw metrics captured

```
RF v0.1.1 train (standard):
  n_train=645 (after filtering 155 invalid ELFs from 800)
  n_test=168  (after filtering 32 from 200)
  accuracy=0.9940476  f1=0.9935484  precision=1.0  recall=0.9871795
  duration=10s
  MLflow run: 0e2865dc0b5b4f67a410cd7035bbcae9
```

---

## Phase 8.2 — CVE-clean DL base image + 2-GPU E2E verified

**Goal:** actually run a training job on 2 GPUs through lolday
end-to-end. Proves `resource_profile=gpu2` → Volcano allocates
2× `nvidia.com/gpu` → container sees `torch.cuda.device_count() == 2`
→ `nn.DataParallel` splits the batch.

**Platform-side changes (this PR):**

- **`charts/lolday/helpers/pytorch-cu12-base/Dockerfile`** — new. Ubuntu 22.04 + CUDA 12.6 runtime + Python 3.12 (deadsnakes) + torch 2.7.0 + scientific stack + `islab-malware-detector`. Trivy-scanned clean (0 Critical). Published as `lolday/pytorch-cu12-base:2.7.0-cu126`.
- **Convention change for DL detectors**: `FROM harbor.lolday.svc:80/lolday/pytorch-cu12-base:2.7.0-cu126`. Detector `pyproject.toml` declares torch for local dev only; Dockerfile does `pip install --no-deps .` so kaniko snapshots only the thin detector layer.
- **`scripts/migrate-ephemeral-to-ssd.sh`** — staged migration of Docker, K3s containerd, kubelet from `/` to `/mnt/ssd500g` NVMe. Explicitly documents that Stage 4 must NOT touch `/var/lib/rancher/k3s/storage` (Harbor registry + MLflow artifact PVs live there) — Phase 8.2 live-fire run DID touch that area and wiped all non-postgres PVs, see "Stage 4 data-loss post-mortem" below.
- **`scripts/recover-harbor.sh`** — rebuilds Harbor projects + robot account + kubernetes pull secret + re-pushes all core platform images when Harbor state is lost.
- **Several diagnostic scripts** (`disk-diag`, `diag-backend-401`, `diag-pv-data`, `harbor-inventory`, `find-lost-data`) — read-only introspection used during the Phase 8.2 incident.

**Detector-side (bolin8017/elfcnndet v0.2.1):**

- Dockerfile reduced to 3 RUN lines. Base image provides Python 3.12 + torch + all transitive deps; only the detector package is installed on top (`pip install --no-deps .`).
- Build time on lolday: **~2 minutes** (was previously failing after 10+ min at Kaniko OOM / CVE-block).
- Trivy: **0 Critical, 0 High**.

**2-GPU verification artifacts (job `ef5d6082`, 2026-04-21 12:40):**

```
pod spec:   nvidia.com/gpu: 2                 # Volcano honoured gpu2 profile
detector:   data_parallel_enabled gpus=2      # structlog inside the container
MLflow run 87b151e0951b4654a5a318b087fe10b7:
  gpu.device_count  = 2
  gpu.device_names  = NVIDIA GeForce RTX 2080 Ti,NVIDIA GeForce RTX 2080 Ti
summary_metrics:
  train_acc=0.969  train_loss=0.086  duration=2.75s  (20 epochs, 645 samples)
```

Subsequent evaluate (gpu2) → F1=0.969 / accuracy=0.970 on 168 test
samples. Predict (gpu2) → succeeded, predictions.csv emitted.

## Stage 4 data-loss post-mortem

During migration to SSD I moved `/var/lib/kubelet` en bloc. On K3s
restart, the kubelet's reconciler re-created mount points for every
`local-volume` PV, but the hostpath targets under
`/var/lib/rancher/k3s/storage/pvc-XXX` got re-provisioned AS EMPTY
DIRS — almost certainly because the bind-mounted kubelet pod-volume
bookkeeping under `/var/lib/kubelet.old` held stale file references
that, when lazily unmounted, confused local-path-provisioner's
"directory already exists" check. Net effect: Harbor registry, MLflow
artifacts, Grafana, Prometheus TSDB, Alertmanager, Trivy scan db all
reset to empty. Postgres (lolday + harbor_db) and Redis + Loki
survived (likely because their pods held file locks through the
transition).

Recovery: `scripts/recover-harbor.sh` rebuilt everything pushable;
Alembic tables were re-created via a one-shot
`Base.metadata.create_all` + `alembic stamp head` from a throwaway
backend pod; MLflow DB + user re-created manually per the Phase 4
runbook. First-admin user bootstrapped by backend on startup. Total
lost data was test-scale (jobs + MLflow runs from a few days of E2E
testing).

**Takeaway for Stage 4 of the migration script:** future runs must
either (a) also migrate `/var/lib/rancher/k3s/storage` in the same
atomic operation, or (b) pre-emptively scale all stateful workloads
to zero before kubelet is stopped. The script header now documents
both paths.
