# Phase 11: Detector Framework v1 — Design Specification

## Overview

Phase 11 rebuilds the detector framework and the platform-detector contract from scratch. Today's `islab-malware-detector` v0.5 gives a single abstract base class with three methods (`train`, `evaluate`, `predict`) and a Typer CLI wrapper. Every detector reimplements data loading, feature extraction, metrics, training loops, and DL concerns (multi-GPU, checkpointing, early stopping). The two reference detectors (`elfrfdet`, `elfcnndet`) duplicate ~100 lines of identical code and `elfcnndet` hand-rolls a PyTorch training loop that `nn.DataParallel` — a framework-deprecated pattern — splits across GPUs.

The platform contract is equally thin: lolday's backend writes `config.json` to `/mnt/config/`, the detector binary reads fixed mount paths, and progress flows only through `kubectl logs`. No schema versioning, no capability introspection, no structured events.

This phase replaces both.

**Goal:** A lolday operator registers a detector image; the backend reads the detector's capability manifest from an OCI label, validates resource compatibility, submits a Volcano Job; the detector runs `maldet run train`; a sidecar tails structured events into the backend DB; the UI shows live metric charts; MLflow gets the run as before; DDP replaces DataParallel. Detector authors write only the business logic (feature extractor + model); the framework owns everything else.

**Constraints:**

- Breaking changes encouraged. v0.5 detectors will not keep working. The system has no production workload to preserve.
- Open-source stack only. No custom re-implementations where a mainstream library fits.
- English-ecosystem libraries (Taiwan lab preference; avoids China-origin dependencies).
- Framework must support both classical ML (scikit-learn) and deep learning (PyTorch Lightning) natively, not by forcing one idiom onto the other.
- Platform contract must be machine-readable so the backend can introspect and validate before running containers.

---

## Scope

### In scope

1. **`maldet` v1.0 framework package** (new PyPI package, new GitHub repo). Contains Protocols, builtin readers/predictors/evaluators, Trainer engines (Sklearn + Lightning), CLI, EventLogger, Hydra integration, scaffold command.
2. **Capability manifest standard** (`maldet.toml` in source, OCI image label, `manifest.json` in artifacts).
3. **Structured event stream** (`/mnt/output/events.jsonl`) with sidecar tail → lolday DB.
4. **Hydra + hydra-zen + Pydantic config system** replacing v0's Pydantic Settings.
5. **Platform contract redesign** on the lolday backend: `services/job_spec.py`, `services/job_config.py`, `services/harbor.py` (read OCI labels), new `services/events_tail.py`, new `models/job_event.py` + Alembic migration, new `/jobs/{id}/events` endpoint with WebSocket.
6. **Reference detectors rewritten**: `elfrfdet` v2.0.0 (sklearn), `elfcnndet` v2.0.0 (Lightning + DDP).
7. **Frontend**: live metric chart on `/jobs/{id}` page, subscribed to the event WebSocket.
8. **Retirement of v0 assets**: archive `islab-malware-detector`, wipe v0 Harbor artifacts, mark the v0 PyPI release deprecated.

### Out of scope (deferred)

- **Multi-node distributed training.** server30 is a single node. Lightning supports multi-node without framework changes, but the Volcano Job spec currently creates a single pod. Manifest declares `supports_multinode: false`.
- **Online serving (`maldet serve`).** Framework reserves the entry point and manifest flag; Phase 11 does not implement a FastAPI serving path.
- **Hyperparameter-optimization UI.** CLI `--multirun` works; no backend/UI integration.
- **Active learning / continual learning / streaming ingestion.**
- **Dataset versioning** (DVC, LakeFS, etc.). lolday's `dataset_config` table already tracks dataset references; no second system.
- **Custom ONNX / TorchScript export.** Trainer saves native format only (`.joblib` for sklearn, `.ckpt` for Lightning).
- **Framework support beyond sklearn and PyTorch Lightning.** XGBoost and LightGBM fit through the sklearn interface. TensorFlow / JAX users can add a trainer engine in a future phase.

---

## Architecture

### Technology stack

| Concern            | Choice                                         | Rationale                                                                                                                |
| ------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Language           | Python 3.12+                                   | v0 already requires it; no reason to drop                                                                                |
| Config             | **Hydra** + **hydra-zen**                      | de facto ML config standard (Meta / PyTorch ecosystem); composition and multirun built in                                |
| Validation         | **Pydantic v2**                                | type safety at the platform-facing config boundary                                                                       |
| Classical ML       | **scikit-learn Pipeline**                      | the classical-ML lingua franca; XGBoost and LightGBM fit natively                                                        |
| Deep learning      | **PyTorch Lightning ≥ 2.5**                    | removes the hand-rolled training loop in `elfcnndet`; gives DDP, checkpointing, early stopping, mixed precision for free |
| Metrics            | **torchmetrics** (DL) + `sklearn.metrics` (ML) | torchmetrics is the Lightning-native metric library with correct cross-batch aggregation                                 |
| Tracking           | **MLflow** (keep)                              | lolday already runs MLflow Server; Lightning has official `MLFlowLogger`                                                 |
| CLI                | **Typer** (keep)                               | v0 already uses it; the one `maldet` CLI replaces per-detector CLIs                                                      |
| Scaffolding        | **copier** or built-in Jinja2 template         | `maldet scaffold --template cnn my-detector` generates a working repo                                                    |
| Model registry     | **MLflow Model Registry** (keep)               | lolday's existing `services/model_registry.py` stays                                                                     |
| Serving (deferred) | FastAPI                                        | slot reserved; no Phase 11 work                                                                                          |

### Layered architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Control Plane   maldet.cli (Typer)                         │
│    run train | evaluate | predict | serve (deferred)        │
│    describe | scaffold | check                              │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Pipeline   maldet.pipeline.Stage                           │
│    composition of layers from maldet.toml                   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────┬──────────────┬────────────────────────────────┐
│ 1 Data      │ 2 Features   │ 3 Model                        │
│ SampleReader│ Extractor    │ nn.Module / BaseEstimator      │
├─────────────┼──────────────┼────────────────────────────────┤
│ 4 Trainer   │ 5 Evaluator  │ 6 Predictor                    │
│ SklearnTrnr │ torchmetrics │ BatchPredictor                 │
│ LightningTr │ sklearn.mtrx │ OnlinePredictor (deferred)     │
└─────────────┴──────────────┴────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Artifacts (/mnt/output)                                    │
│    model/  metrics.json  predictions.csv                    │
│    events.jsonl  manifest.json  checkpoints/                │
└─────────────────────────────────────────────────────────────┘
```

Each layer has one responsibility:

- **SampleReader** turns a dataset reference into an iterable of `Sample(sha256, path, label, metadata)`. Does not extract features.
- **FeatureExtractor** turns a `Sample` into an ndarray or tensor. Stateless, pure, cacheable.
- **Model** is the estimator or module definition. Contains no I/O, no training logic.
- **Trainer** runs `fit()`. Serializes the trained model. Two implementations: `SklearnTrainer`, `LightningTrainer`. Each owns its save/load format (joblib / ckpt).
- **Evaluator** computes metrics. Emits a `MetricReport`.
- **Predictor** turns samples into predictions. Batch mode required; online mode deferred.

Protocols (not ABCs) connect layers. A class satisfies a Protocol by matching the signature; no inheritance required. This keeps composition flexible and simplifies test stubbing.

---

## Detector Specification

### Protocols

```python
# maldet.core.protocols
from typing import Protocol, runtime_checkable, Iterator, Any
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

@dataclass(frozen=True)
class Sample:
    sha256: str
    path: Path
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@runtime_checkable
class SampleReader(Protocol):
    def __iter__(self) -> Iterator[Sample]: ...
    def __len__(self) -> int: ...

@runtime_checkable
class FeatureExtractor(Protocol):
    output_shape: tuple[int, ...] | None
    dtype: str
    def extract(self, sample: Sample) -> np.ndarray: ...

@runtime_checkable
class Trainer(Protocol):
    def fit(self, model, train: SampleReader, extractor: FeatureExtractor,
            *, val: SampleReader | None, logger: "EventLogger") -> "TrainResult": ...
    def save(self, result: "TrainResult", out_dir: Path) -> None: ...
    def load(self, model_dir: Path): ...  # returns the trained model

@runtime_checkable
class Evaluator(Protocol):
    def evaluate(self, model, reader: SampleReader, extractor: FeatureExtractor,
                 *, logger: "EventLogger") -> "MetricReport": ...

@runtime_checkable
class Predictor(Protocol):
    def predict(self, model, reader: SampleReader, extractor: FeatureExtractor,
                *, out_path: Path, logger: "EventLogger") -> Path: ...
```

All protocols use `@runtime_checkable` so `isinstance()` works for contract enforcement at pipeline assembly.

### Capability Manifest

Each detector repo carries `maldet.toml` at the root:

```toml
[detector]
name = "elfrfdet"
version = "2.0.0"
framework = "sklearn"                      # sklearn | lightning | sklearn+lightning
description = "Random Forest on first 256 bytes of ELF .text"

[input]
binary_format = "elf"                      # elf | pe | apk | raw_bytes
required_sections = [".text"]
dataset_contract = "sample_csv"            # the platform I/O contract, see below

[output]
task = "binary_classification"             # binary | multiclass | regression | ranking
classes = ["Malware", "Benign"]
score_range = [0.0, 1.0]

[resources]
supports = ["cpu", "gpu1", "gpu2"]         # resource profiles this detector accepts
recommended = "cpu"
min_memory_gib = 2
gpu_required = false

[lifecycle]
stages = ["train", "evaluate", "predict"]
supports_serving = false
supports_hpsweep = true
supports_distributed = false               # false | "ddp" | "fsdp" | "deepspeed"
supports_multinode = false

[artifacts]
model = { path = "model/", type = "dir" }
metrics = { path = "metrics.json", type = "file" }
predictions = { path = "predictions.csv", type = "file" }

[compat]
min_python = "3.12"
min_maldet = "1.0"
schema_version = 1
```

The manifest appears in three places. Each has a distinct purpose:

1. **Source repo** (`maldet.toml`): authored by the detector developer; read by `maldet describe` and `maldet check`.
2. **OCI image label** (`io.maldet.manifest` = base64-encoded JSON). `maldet build` and the lolday build pipeline write this label at image build time. The lolday backend reads it via the Harbor API during `POST /detectors/{id}/builds` without starting the container.
3. **Artifact directory** (`/mnt/output/manifest.json`). Written at runtime by `maldet describe`. Travels with the trained artifacts into MLflow.

### Stage Composition

`maldet.toml` also declares how each stage wires the six layers:

```toml
[stages.train]
reader = "maldet.builtins.readers:SampleCsvReader"
extractor = "elfrfdet.features:Text256Extractor"
model = "elfrfdet.models:make_rf"
trainer = "maldet.trainers:SklearnTrainer"
evaluator = "maldet.evaluators:BinaryClassification"

[stages.evaluate]
reader = "maldet.builtins.readers:SampleCsvReader"
extractor = "elfrfdet.features:Text256Extractor"
evaluator = "maldet.evaluators:BinaryClassification"

[stages.predict]
reader = "maldet.builtins.readers:SampleCsvReader"
extractor = "elfrfdet.features:Text256Extractor"
predictor = "maldet.builtins.predictors:BatchPredictor"
```

Each value is a `module:attribute` entry-point string. `maldet` resolves and instantiates these at runtime.

Defaults reduce boilerplate: `maldet.builtins.defaults` supplies `SampleCsvReader`, `BatchPredictor`, and `BinaryClassification`. A `[stages.default]` block inherits across stages. A minimal `maldet.toml` for a new detector declares only `[detector]`, `[input]`, `[output]`, `[resources]`, and an `extractor` + `model` for each stage.

### I/O Contracts

**`sample_csv` — platform → detector:**

```
columns: file_name,label         (label omitted on predict)
file_name = sha256 hex, 64 chars
sample path = {data.samples_root}/{sha[:2]}/{sha}
label ∈ {"Malware", "Benign"} for binary tasks; class name for multiclass
```

**`metrics.json` — Evaluator output:**

```json
{
  "schema_version": 1,
  "task": "binary_classification",
  "n_samples": 1000,
  "duration_seconds": 12.4,
  "metrics": {
    "accuracy": 0.95,
    "precision": 0.94,
    "recall": 0.96,
    "f1": 0.95,
    "roc_auc": 0.98,
    "pr_auc": 0.97
  },
  "per_class": {
    "Malware": { "precision": 0.94, "recall": 0.96, "support": 500 },
    "Benign": { "precision": 0.96, "recall": 0.94, "support": 500 }
  },
  "confusion_matrix": {
    "labels": ["Benign", "Malware"],
    "matrix": [
      [470, 30],
      [20, 480]
    ]
  },
  "extras": {}
}
```

**`predictions.csv` — Predictor output:**

```
required columns: file_name, pred_label, pred_score
optional columns: pred_prob_<class>, features_used, ...
```

The platform reads the three required columns. Extra columns pass through to MLflow as-is.

### Lifecycle

**train**

1. CLI reads `/mnt/config/config.yaml` (Hydra composes it).
2. `SampleReader` yields samples. `FeatureExtractor` transforms each into a vector or tensor. Framework materializes a matrix (sklearn) or a `DataLoader` (Lightning).
3. `Trainer.fit(model, ...)` runs the training. It emits `stage_begin`, `epoch_begin`, `metric`, `epoch_end`, `stage_end` events through `EventLogger`.
4. `Trainer.save(result, /mnt/output/model/)` writes the artifact in its native format.
5. If val data exists, the framework runs `Evaluator` on val and writes `metrics.json`.
6. CLI exits 0 on success, non-zero on failure. The sidecar's last event determines platform-side status.

**evaluate**

1. CLI reads config. The platform has placed the trained model at `/mnt/source-model/`.
2. `Trainer.load(/mnt/source-model/)` returns the model object.
3. `Evaluator.evaluate()` runs on test data and writes `metrics.json`.

**predict**

1. CLI reads config. Model at `/mnt/source-model/`, input CSV at `/mnt/config/predict.csv`.
2. `Trainer.load()` + `Predictor.predict()` → `/mnt/output/predictions.csv`.

---

## Platform Contract

### CLI Surface

```
maldet run train      [--config PATH] [--override KEY=VAL ...]
maldet run evaluate   [--config PATH] [--override KEY=VAL ...]
maldet run predict    [--config PATH] [--override KEY=VAL ...]
maldet serve          [--config PATH] [--port 8080]          # deferred
maldet describe       [--format json|toml]
maldet scaffold       [--template rf|cnn|transformer] NAME
maldet check          [--config PATH]
```

`maldet` is a single framework-owned CLI installed by the `maldet` package. The CLI finds the detector's `maldet.toml` in this order at startup:

1. `$MALDET_MANIFEST` environment variable, if set (absolute path).
2. `./maldet.toml` in the current working directory.
3. `/app/maldet.toml` (the scaffold's Docker `WORKDIR`).

It resolves `_target_` strings using `importlib.import_module`. The detector's Python package must be installed (scaffolded Dockerfile runs `pip install .`) so `import elfrfdet.features` works at runtime. One `maldet.toml` per container — no multi-detector discovery, no entry-point ceremony.

### Config Delivery (Hydra)

The lolday backend writes `/mnt/config/config.yaml`:

```yaml
defaults:
  - _self_
  - stage: train
  - feature: text256
  - model: rf
  - trainer: sklearn
  - evaluator: binary_classification

paths:
  config_dir: /mnt/config
  output_dir: /mnt/output
  samples_root: /mnt/samples
  source_model: /mnt/source-model

data:
  train_csv: ${paths.config_dir}/train.csv
  test_csv: ${paths.config_dir}/test.csv
  predict_csv: ${paths.config_dir}/predict.csv

mlflow:
  tracking_uri: null # set from env MLFLOW_TRACKING_URI
  run_id: null
  experiment_id: null
```

Hydra loads and composes. User-supplied overrides append to the CLI args:

```
maldet run train --config /mnt/config/config.yaml \
  +model.n_estimators=500 \
  +trainer.n_jobs=4
```

For hyperparameter search:

```
maldet run train --config ... --multirun \
  +model.n_estimators=100,500,1000 \
  +model.max_depth=null,10,20
```

This produces 9 runs, one MLflow run each, under `multirun/run_0/`, `run_1/`, …

After composition, `hydra-zen.instantiate()` builds the object graph from `_target_` nodes. Pydantic validates the `paths`, `data`, and `mlflow` blocks — the platform-facing contract — and passes the whole object tree to `StageRunner`.

### Event Stream

The detector emits one NDJSON line per event to `/mnt/output/events.jsonl`. Each write is followed by `fsync` to survive pod kills.

Full event kind set:

- `stage_begin` — stage name, config hash
- `data_loaded` — sample counts
- `epoch_begin` / `epoch_end` — epoch index, duration
- `metric` — metric name, value, step
- `artifact_written` — path, size
- `checkpoint_saved` — path, monitored metric value
- `warning` — message, context
- `error` — message, traceback
- `stage_end` — stage name, status (`success` | `failure`), exit code

A sidecar container in the Volcano Job task tails this file. The sidecar image is an extension of the existing `job-helper:v2` image. The sidecar posts each event to the lolday backend's internal endpoint (`POST /internal/jobs/{job_id}/events`), authenticated by the job token from `JOB_TOKEN` env (reuses the Phase 4 pattern).

The backend persists events in a new `job_events` table and relays them to frontend subscribers over WebSocket.

### Dataset Mount Contract

| Mount                | Purpose                                                                                      | Writer                                     |
| -------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------ |
| `/mnt/config/`       | `config.yaml`, `train.csv`, `test.csv`, `predict.csv`                                        | init container `config-writer`             |
| `/mnt/samples/`      | dataset root (flat SHA layout)                                                               | samples PV (read-only)                     |
| `/mnt/source-model/` | trained model for evaluate / predict                                                         | init container `model-fetcher` (read-only) |
| `/mnt/output/`       | `model/`, `metrics.json`, `predictions.csv`, `events.jsonl`, `manifest.json`, `checkpoints/` | detector container                         |

### OCI Image Labels

The scaffold-generated `Dockerfile` declares the labels via build-time arguments:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml maldet.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

ARG MALDET_NAME
ARG MALDET_VERSION
ARG MALDET_FRAMEWORK
ARG MALDET_MANIFEST_B64
ARG GIT_COMMIT

LABEL org.opencontainers.image.title="${MALDET_NAME}"
LABEL org.opencontainers.image.version="${MALDET_VERSION}"
LABEL org.opencontainers.image.revision="${GIT_COMMIT}"
LABEL io.maldet.manifest.schema="1"
LABEL io.maldet.manifest="${MALDET_MANIFEST_B64}"
LABEL io.maldet.framework="${MALDET_FRAMEWORK}"

ENTRYPOINT ["maldet"]
```

The lolday build pipeline (BuildKit, Phase 9.3 onward) computes these build-args before invoking `buildctl`:

1. The build-helper container clones the detector source.
2. It runs `maldet check` to validate `maldet.toml`.
3. It runs `maldet describe --format json | base64 -w0` to produce the manifest string.
4. It passes `MALDET_NAME`, `MALDET_VERSION`, `MALDET_FRAMEWORK`, `MALDET_MANIFEST_B64`, and `GIT_COMMIT` through `buildctl ... --opt build-arg:<KEY>=<VALUE>`.

The alternative `buildctl --opt label:io.maldet.manifest=<value>` injects labels without touching the Dockerfile, but keeping the `ARG`/`LABEL` pair in the Dockerfile makes local `docker build` work the same way for detector developers.

`maldet describe` and `maldet check` also run locally (e.g., in the detector repo's CI) so developers catch manifest errors before pushing.

### lolday Backend Changes

| File                               | Change                                                                                                                                                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/job_spec.py`             | Detector container `command=["maldet","run",stage,"--config","/mnt/config/config.yaml"]`. Add sidecar container `event-tailer` to the Volcano Job task template.                                                 |
| `services/job_config.py`           | `JobConfigRenderer` writes YAML (not JSON) and renders the Hydra YAML above. Platform overrides pass via container `args`.                                                                                       |
| `services/harbor.py`               | `get_artifact` reads image config `Labels` field; extracts `io.maldet.manifest`, decodes, validates against a Pydantic `DetectorManifest` model, and persists to `detector_version.manifest` (new JSONB column). |
| `services/validator.py`            | New pre-flight: reject build if manifest is absent; reject job if `resource_profile` not in `manifest.resources.supports` or `dataset_contract` not in the platform's supported list.                            |
| `services/events_tail.py`          | New. Receives events from sidecar via HTTP and persists to `job_events`.                                                                                                                                         |
| `models/job_event.py`              | New. ORM for `job_events` (id, job_id, ts, kind, payload JSONB, indexed on `(job_id, ts)`).                                                                                                                      |
| Alembic migration                  | Adds `job_events` table; adds `detector_version.manifest` JSONB column.                                                                                                                                          |
| `routers/internal.py`              | New `POST /internal/jobs/{job_id}/events` endpoint. Job-token auth.                                                                                                                                              |
| `routers/jobs.py`                  | New `GET /jobs/{id}/events?since=<ts>` (paged fetch) and `WS /jobs/{id}/events` (live stream). Status determination switches from "Volcano Job phase" to "`stage_end.status`" (with Volcano phase as fallback).  |
| `frontend/src/pages/JobDetail.tsx` | New live metric chart (Recharts) subscribed to the WebSocket.                                                                                                                                                    |

---

## Config Management

### Tool split

| Tool            | Scope                                                                                                               |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Hydra**       | YAML loading, composition via `defaults:`, CLI overrides, multirun sweeps                                           |
| **hydra-zen**   | Generates structured configs from Python dataclasses and factories; zero duplication between config schema and code |
| **Pydantic v2** | Validates the platform-facing blocks (`paths`, `data`, `mlflow`); friendly error messages                           |

This triple is the current mainstream in the PyTorch ecosystem. Lightning tutorials use it. hydra-zen removes the "config dataclass duplicates the function signature" pain point that earlier Hydra users hit.

### Config tree layout (each detector repo)

```
elfrfdet/
├── maldet.toml
├── conf/
│   ├── config.yaml
│   ├── stage/
│   │   ├── train.yaml
│   │   ├── evaluate.yaml
│   │   └── predict.yaml
│   ├── model/
│   │   └── rf.yaml
│   ├── trainer/
│   │   └── sklearn.yaml
│   ├── evaluator/
│   │   └── binary_classification.yaml
│   └── feature/
│       └── text256.yaml
└── src/elfrfdet/...
```

Framework enforces the groups (`model`, `trainer`, `feature`, `evaluator`, `stage`). Advanced authors can add groups.

### Example files

`conf/model/rf.yaml`:

```yaml
_target_: elfrfdet.models.make_rf
n_estimators: 100
max_depth: null
min_samples_split: 2
min_samples_leaf: 1
random_state: 42
n_jobs: -1
```

`conf/feature/text256.yaml`:

```yaml
_target_: elfrfdet.features.Text256Extractor
size: 256
pad_value: 0
```

The detector's Python code defines `make_rf` and `Text256Extractor` as ordinary callables. Hydra instantiates them. The detector author writes no config-parsing code.

### Environment variable escape hatch

Hydra supports `${oc.env:VAR,default}`. `config.yaml` uses it for a few platform-injected values:

```yaml
mlflow:
  tracking_uri: ${oc.env:MLFLOW_TRACKING_URI,null}
  run_id: ${oc.env:MLFLOW_RUN_ID,null}
```

The primary path stays YAML + CLI override.

---

## Experiment and Training Management

### EventLogger fan-out

`StageRunner` constructs a `CompositeEventLogger` that wraps three concrete loggers:

- `MlflowEventLogger` — calls `mlflow.log_metric`, `mlflow.log_param`, `mlflow.log_artifact`.
- `JsonlEventLogger` — writes NDJSON to `/mnt/output/events.jsonl`, `fsync` per event.
- `StdoutEventLogger` — writes `maldet.event: {json}` lines to stdout as a fallback (useful in local dev when no sidecar runs).

Detector code sees one `EventLogger` object. A single `logger.log_metric("train_loss", 0.34, step=epoch)` fans out to all three.

### Lightning integration

`maldet.trainers.lightning.LightningTrainer` wraps `lightning.Trainer`:

- `MaldetLightningLogger` (subclass of `lightning.pytorch.loggers.Logger`) delegates to the framework's `EventLogger`. `LightningModule.log(name, value, prog_bar=True)` flows into MLflow and `events.jsonl` automatically.
- `MaldetProgressCallback` (subclass of `lightning.pytorch.callbacks.Callback`) emits `epoch_begin`, `epoch_end`, `checkpoint_saved` events.
- Built-in callbacks: `ModelCheckpoint(dirpath=/mnt/output/checkpoints/, monitor="val_loss", save_top_k=3)`, `EarlyStopping(monitor="val_loss", patience=5)`. Config in `conf/trainer/lightning.yaml` can override these.
- `Trainer.save` copies the best checkpoint to `/mnt/output/model/` so evaluate and predict see a stable path.

### Sklearn integration

`SklearnTrainer.fit()`:

```python
def fit(self, model, train, extractor, *, val=None, logger):
    logger.log_event("stage_begin", stage="train")
    logger.log_params(model.get_params())
    X, y = _materialize(train, extractor)
    logger.log_event("data_loaded", n_train=X.shape[0])
    t0 = time.time()
    model.fit(X, y)
    logger.log_metric("train_time_seconds", time.time() - t0)
    if val is not None:
        Xv, yv = _materialize(val, extractor)
        logger.log_metric("val_accuracy", accuracy_score(yv, model.predict(Xv)))
    logger.log_event("stage_end", status="success")
    return TrainResult(model=model)

def save(self, result, out_dir):
    joblib.dump(result.model, out_dir / "model.joblib")
```

Both Trainer implementations emit the same event kind set. The platform cannot and need not tell them apart.

### Model Registry

`maldet run train` does not register models in the MLflow Model Registry. Registration remains a lolday backend responsibility. The reconciler, on receiving a successful `stage_end`, calls `mlflow.register_model()` as it does today. This keeps the detector blind to lab-specific release gates.

### UI / Visualization

Two existing channels carry visualization. Phase 11 does not add a new system.

- **Live, during training** — lolday frontend `/jobs/{id}` subscribes to the `/jobs/{id}/events` WebSocket. A Recharts line plot renders the `metric` events, a progress bar renders `epoch_begin`/`end`, and a log tail renders `warning`/`error` events.
- **Historical, after training** — MLflow UI through `experiments_proxy.py` (Phase 4 ships this). MLflow handles run comparison, metric tables, and artifact browsing.

---

## GPU and Distributed

### Resource declaration to Trainer configuration

The detector manifest declares `resources.supports = ["cpu", "gpu1", "gpu2"]` and `lifecycle.supports_distributed = "ddp"`.

The lolday Volcano Job injects container env:

```
MALDET_RESOURCE_PROFILE=gpu2
MALDET_GPU_COUNT=2
MALDET_DISTRIBUTED_STRATEGY=ddp
```

`LightningTrainer` reads these and constructs `lightning.Trainer` arguments:

| Env                                                    | →   | Lightning args                                  |
| ------------------------------------------------------ | --- | ----------------------------------------------- |
| `MALDET_GPU_COUNT=0`                                   | →   | `accelerator="cpu"`                             |
| `MALDET_GPU_COUNT=1`                                   | →   | `accelerator="gpu", devices=1, strategy="auto"` |
| `MALDET_GPU_COUNT=2, MALDET_DISTRIBUTED_STRATEGY=ddp`  | →   | `accelerator="gpu", devices=2, strategy="ddp"`  |
| `MALDET_GPU_COUNT=2, MALDET_DISTRIBUTED_STRATEGY=fsdp` | →   | `accelerator="gpu", devices=2, strategy="fsdp"` |

`SklearnTrainer` ignores the GPU env vars. The detector's manifest should declare `resources.supports = ["cpu"]` so the platform never allocates GPUs to an sklearn detector.

### DDP replaces DataParallel

Phase 8's `elfcnndet` uses `nn.DataParallel`. PyTorch has deprecated `DataParallel` since version 1.6 (GIL bottleneck, slow gradient sync, uneven memory). The v1 framework supports only DDP, FSDP, and DeepSpeed — all through Lightning's `strategy=` argument. Detector code declares the strategy in the manifest; Lightning handles process launch, rank assignment, gradient sync, and rank-0-only checkpoint writing.

### Multi-node

Deferred. Lightning supports multi-node DDP without detector changes — it reads `MASTER_ADDR` / `MASTER_PORT` / `NODE_RANK` from the environment. The Volcano Job spec needs `tasks[].replicas=N, minAvailable=N` to launch N pods with gang scheduling. A separate phase adds this. Until then, manifests declare `supports_multinode = false`.

### Mixed framework stacks

`framework = "sklearn+lightning"` is valid. A detector can run Lightning training and sklearn post-processing in predict. Each stage picks its trainer independently through `maldet.toml`.

---

## Migration

### Phase 11 sub-phases

| Sub-phase                            | Scope                                                                         | Deliverables                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------ | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **11a — `maldet` v1 framework**      | New repo (`bolin8017/maldet` or a new name)                                   | `maldet` v1.0 on PyPI; Protocols; StageRunner; builtins (`SampleCsvReader`, `BatchPredictor`, `BinaryClassification`); CLI; EventLogger; `SklearnTrainer`; `LightningTrainer`; `maldet scaffold`; ≥ 80% unit test coverage; mkdocs documentation site                                                                                                                                                                                |
| **11b — lolday backend v1 contract** | lolday repo                                                                   | Rewrites in `services/job_spec.py`, `services/job_config.py`, `services/harbor.py`, `services/validator.py`. New `services/events_tail.py`, `models/job_event.py`, `/internal/jobs/{id}/events`, `/jobs/{id}/events` (paged + WebSocket). New Alembic migration for `job_events` and `detector_version.manifest`. Frontend live metric chart.                                                                                        |
| **11c — template detectors v2**      | `elfrfdet` + `elfcnndet` repos (names kept, internals replaced, `v2.0.0` tag) | `elfrfdet` new implementation: `features.Text256Extractor` + `models.make_rf` + `maldet.toml`. ~30 lines of business logic. `elfcnndet` new implementation: `features.Text256Extractor` + `ByteCNN` `LightningModule`; manifest declares `supports_distributed = "ddp"`. Dockerfiles switch entrypoint to `maldet`. READMEs updated.                                                                                                 |
| **11d — E2E and retirement**         | lolday server30                                                               | Build `elfrfdet:v2.0.0` and `elfcnndet:v2.0.0` images, register, run full train→evaluate→predict with event stream, verify live metrics UI and MLflow artifacts, run 2-GPU DDP verification on `elfcnndet` (replaces Phase 8's DataParallel run), delete all v0 Harbor artifacts, mark the `islab-malware-detector` PyPI release deprecated, archive the `islab-malware-detector` GitHub repo. Write `docs/phase11-e2e-findings.md`. |

### Dependency graph

```
    Phase 11a (maldet framework)
        │
        ├──────────────────► Phase 11c (templates)
        │                           │
Phase 11b (backend contract)        │
        │                           │
        └───────────┬───────────────┘
                    ▼
            Phase 11d (E2E)
```

11a and 11b can run in parallel because both build against this spec as their shared contract. 11c depends on 11a (templates import `maldet`). 11d requires all three.

### Retired assets

| Asset                                                                    | Disposition                                                          |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `islab-malware-detector` v0.5 on PyPI                                    | Marked deprecated; README redirects to `maldet`; no further releases |
| `islab-malware-detector` GitHub repo                                     | Archived                                                             |
| `elfrfdet`, `elfcnndet` GitHub repos                                     | Contents replaced; `v2.0.0` tag; repo names kept                     |
| Harbor v0 detector artifacts (`elfrfdet:0.1.1`, `elfcnndet:0.2.1`, …)    | Deleted after 11d E2E                                                |
| `upxelfdet` package and artifacts                                        | Deleted after 11d                                                    |
| lolday DB rows in `detector`, `detector_version`, `build` referencing v0 | Hard-deleted before 11d E2E                                          |

### YAGNI (explicit non-goals)

- Multi-node distributed training
- Online serving (`maldet serve` implementation)
- Active / continual / streaming learning
- Dataset versioning layer (DVC, LakeFS)
- HPO sweep UI
- A/B testing or shadow traffic

---

## Open Questions and Future Work

### Open during Phase 11 implementation

- **`maldet` package name.** `maldet` matches the v0 `maldet` import path used by `islab-malware-detector`; keeping it simplifies migration for anyone who copied that import style. Alternatives: `maldetector`, `islab-maldet`. Decide at 11a kickoff.
- **`copier` vs. in-framework Jinja2 for scaffolding.** `copier` is the modern `cookiecutter` successor; in-framework Jinja2 has no extra dep. `copier` wins if we want scaffold _updates_ over time. Decide at 11a design.
- **Event sidecar vs. backend pull.** Current design has a sidecar POST events to the backend. An alternative is the backend `exec`-tailing the file, as Phase 4 does for `kubectl logs`. Sidecar is cleaner for rate-limiting and retry; `exec` has no extra container. Sidecar selected for v1 and can degrade to `exec` if sidecar overhead becomes an issue.

### Future phases

- **Multi-node DDP** — needs Volcano multi-pod job spec + framework env wiring verification.
- **Online serving** — FastAPI-based `maldet serve`; integration with a future lolday serving endpoint.
- **HPO UI** — backend generates `--multirun` sweeps; frontend shows a sweep run tree.
- **ONNX / TorchScript export** — add a `Trainer.export()` hook and a manifest `[export]` block.
- **Additional framework engines** — TensorFlow, JAX via `TFTrainer` / `JaxTrainer` implementations that satisfy the `Trainer` protocol.
