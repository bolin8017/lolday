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

| # | Finding | Sev | Fixed here | Follow-up |
|---|---------|-----|-----------|-----------|
| 1 | `GET /api/v1/builds/<id>` does not exist — path is nested under detector | S3 | **✅ flat alias** | — |
| 2 | Harbor Trivy scan never auto-triggers; builds sit at `scanning` forever | S1 | **✅ reconciler auto-triggers** | — |
| 3 | Build validator `/tmp` EmptyDir 512Mi → any torch detector evicted | S1 | **✅ validator redesign (AST + --no-deps)** | — |
| 4 | Validator RSS 2Gi → OOM on torch install | S1 | **✅ same as #3** | — |
| 5 | Kaniko container RSS 4Gi → DL snapshot OOMs | S1 | **✅ 20Gi** | Switch to BuildKit long-term |
| 6 | Kaniko triggers **node-level ephemeral-storage eviction** building DL detectors | S1 | **✅ ephemeral-storage req/limit + 1h TTL for failed builds** | — |
| 7 | 19 % of samples have no `.text` section / fail to parse | S2 | **✅ elfrfdet+elfcnndet catch ELFError** | Revisit dataset curation (statically linked Go / heavily stripped ELFs) |
| 8 | Build concurrency-limit 429 does not state the limit | S3 | **✅ includes `limit` + `in_flight`** | — |
| 9 | DL detector image has 1 Critical + 9 High CVE from torch transitive deps | N/A | — (expected) | Detector author picks a CVE-clean CUDA base image |

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

### Recommended follow-up (deferred — out of this PR's scope)

- **Short term:** add `ephemeral-storage: requests=4Gi, limits=16Gi` to
  both the validate and kaniko containers so they don't evict cheaply.
  Emit a warning from the build pipeline when an image layer is
  projected to exceed N GiB.
- **Medium term:** change `maldet_validator.py` to install the detector
  with `--no-deps` and only import the `config_class` module. If the
  config module itself imports heavyweight deps, publish a convention:
  detector authors split `config.py` (pydantic only) from `detector.py`
  (ML stack).
- **Long term:** replace Kaniko with BuildKit rootless or with a
  dedicated builder node. Kaniko's full-filesystem snapshot model scales
  poorly to DL-era dependency footprints.

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
