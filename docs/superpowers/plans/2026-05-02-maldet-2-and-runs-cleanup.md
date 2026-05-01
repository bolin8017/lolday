# maldet 2.0 + Lolday Runs UX cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate maldet's label-encoding inconsistency, make the Job Detail confusion-matrix / per-class cards actually render, fix artifact-download filename, and remove the redundant `/runs/:expId/:runId` page — coordinated across the maldet upstream, Lolday repo, and the production cluster.

**Architecture:** Two-repo work (maldet + lolday) followed by a single-window operator cutover that wipes all historical Job/MLflow data and re-establishes baselines via rebuilt detector images. No backwards compatibility; manifest schema bumps to 2 and trainers gain a required `classes` kwarg.

**Tech Stack:** Python 3.12 + Pydantic + sklearn + Lightning (maldet); FastAPI + uv (lolday backend); React + Vite + TanStack Query + Vitest + Playwright (lolday frontend); Helm + K3s + Postgres + MinIO + MLflow (cluster).

**Spec:** `docs/superpowers/specs/2026-05-01-maldet-2-and-runs-cleanup-design.md`

---

## Working directories

- **`MALDET_REPO`** — operator's clone of `islab-malware-detector` (the maldet PyPI package). Tasks under "Phase 1" assume this is the cwd.
- **`LOLDAY_REPO`** — the lolday repo (this one). Tasks under "Phase 2/3" assume this is the cwd.
- **`SERVER30`** — production cluster operator session (kubectl + mc + curl access). Tasks under "Phase 4" run here.

Each task that is not in `LOLDAY_REPO` says so in the **Files** block.

## File structure (Lolday-side)

| Path                                                                                                                                                               | Responsibility                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| `backend/pyproject.toml`, `backend/uv.lock`                                                                                                                        | Bump `maldet>=2.0,<3`                                     |
| `charts/lolday/helpers/build-helper/pyproject.toml`, `charts/lolday/helpers/build-helper/uv.lock`                                                                  | Bump `maldet[lightning]>=2.0,<3`                          |
| `backend/app/routers/experiments_proxy.py`                                                                                                                         | RFC 6266 `Content-Disposition` for `download_artifact`    |
| `backend/app/config.py`                                                                                                                                            | New `BACKEND_MAINTENANCE_MODE: bool` setting              |
| `backend/app/routers/jobs.py`                                                                                                                                      | Maintenance gate at `POST /jobs`                          |
| `backend/tests/conftest.py`, `backend/tests/test_services_validator.py`, `backend/tests/test_services_validator_phase11b.py`, `backend/tests/test_routers_jobs.py` | Add `positive_class: "Malware"` to manifest fixtures      |
| `backend/tests/test_routers_experiments_proxy.py` (new)                                                                                                            | Download endpoint regression tests                        |
| `frontend/src/routes/_authed.runs.$expId.$runId.tsx`                                                                                                               | Replaced: now a redirect component                        |
| `frontend/src/routes/_authed.jobs.$id.tsx`                                                                                                                         | Drop "Open run ↗" tab                                     |
| `frontend/src/components/jobs/JobDetailShell.tsx`                                                                                                                  | Add `OpenInMlflowButton` to header                        |
| `frontend/src/routes/_authed.runs.$expId.tsx`                                                                                                                      | Inline run-cell link routing; remove redundant Job column |
| `frontend/src/components/common/ArtifactTree.tsx`                                                                                                                  | `<a download={name}>`                                     |
| `frontend/src/components/jobs/PredictSummary.tsx`                                                                                                                  | `<a download="predictions.csv">`                          |
| `frontend/src/i18n/zh-TW.json`, `frontend/src/i18n/en.json`                                                                                                        | `common.openInMlflow` key (nested)                        |
| `scripts/wipe-mlflow-history.sh` (new)                                                                                                                             | Soft-delete MLflow + `mlflow gc`                          |

## File structure (maldet-side, in MALDET_REPO)

| Path                                   | Responsibility                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------ |
| `maldet/manifest.py`                   | `OutputConfig.positive_class` field + validator; `CompatConfig.schema_version=2`     |
| `maldet/_version.py`                   | `2.0.0`                                                                              |
| `maldet/protocols.py`                  | `Trainer.fit(..., classes: Sequence[str], ...)`                                      |
| `maldet/trainers/sklearn_trainer.py`   | `classes.index(label)` encoding                                                      |
| `maldet/trainers/lightning_trainer.py` | Same                                                                                 |
| `maldet/runner.py`                     | Pass `classes=` to trainer; pass `positive_class=` to evaluator from manifest        |
| `maldet/evaluators/binary.py`          | Aligned CM labels/matrix orientation; emit `confusion_matrix` and `per_class` events |
| `maldet/events/kinds.py`               | `CONFUSION_MATRIX` and `PER_CLASS` enum members + required-field map                 |
| `tests/...`                            | Per §8.1 of spec                                                                     |
| `pyproject.toml`                       | `version = "2.0.0"`                                                                  |
| `CHANGELOG.md`                         | 2.0.0 entry with migration note                                                      |

---

# Phase 1 — maldet 2.0 (upstream library)

> All Phase 1 tasks run in `MALDET_REPO`. Existing `pytest` config and import paths assumed; copy your local virtualenv pattern (e.g. `uv run pytest`).
>
> **Audit on 2026-05-02:** maldet repo at `/home/bolin8017/Documents/repositories/maldet` is already at v1.2.0 with the following spec items already merged on main — **SKIP the corresponding tasks below**:
>
> - ✅ Task 1.1 — `CONFUSION_MATRIX` and `PER_CLASS` are in `EventKind` enum
> - ✅ Task 1.7 — `BinaryClassification.evaluate` writes `confusion_matrix.labels = [other, self._positive]` matching the `labels=[0, 1]` matrix orientation
> - ✅ Task 1.8 — evaluator emits `confusion_matrix` and `per_class` events
>
> **Source layout:** `src/maldet/...` (src layout). Anywhere this plan says `MALDET_REPO/maldet/foo.py`, read it as `MALDET_REPO/src/maldet/foo.py`.
>
> **Test layout:** `tests/<area>/test_*.py` (e.g., `tests/evaluators/test_binary.py`, `tests/trainers/test_*.py`). New test files for tasks below should live in the matching subdirectory.
>
> **Version baseline:** maldet HEAD is 1.2.0; the bump is `1.2.0 → 2.0.0`.

### Task 1.1: Add `CONFUSION_MATRIX` and `PER_CLASS` to `EventKind` — **SKIPPED (already merged)**

**Files:**

- Modify: `MALDET_REPO/maldet/events/kinds.py`
- Test: `MALDET_REPO/tests/test_event_kinds.py` (new file or extend existing)

- [ ] **Step 1: Write failing test for new event kinds**

```python
# tests/test_event_kinds.py
from maldet.events.kinds import EventKind, validate_payload


def test_confusion_matrix_event_kind_exists():
    assert EventKind.CONFUSION_MATRIX.value == "confusion_matrix"


def test_per_class_event_kind_exists():
    assert EventKind.PER_CLASS.value == "per_class"


def test_confusion_matrix_payload_validates():
    validate_payload(
        EventKind.CONFUSION_MATRIX,
        {"labels": ["Benign", "Malware"], "matrix": [[1, 0], [0, 1]]},
    )


def test_confusion_matrix_payload_missing_labels_raises():
    import pytest
    with pytest.raises(ValueError, match="'labels'"):
        validate_payload(EventKind.CONFUSION_MATRIX, {"matrix": [[1, 0], [0, 1]]})


def test_per_class_payload_validates():
    validate_payload(
        EventKind.PER_CLASS,
        {"per_class": {"Malware": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 10}}},
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_kinds.py -v`
Expected: FAIL — `AttributeError: CONFUSION_MATRIX` or `KeyError`

- [ ] **Step 3: Add the two enum members and required-field rules**

```python
# maldet/events/kinds.py
class EventKind(StrEnum):
    STAGE_BEGIN = "stage_begin"
    STAGE_END = "stage_end"
    DATA_LOADED = "data_loaded"
    EPOCH_BEGIN = "epoch_begin"
    EPOCH_END = "epoch_end"
    METRIC = "metric"
    ARTIFACT_WRITTEN = "artifact_written"
    CHECKPOINT_SAVED = "checkpoint_saved"
    WARNING = "warning"
    ERROR = "error"
    CONFUSION_MATRIX = "confusion_matrix"  # NEW
    PER_CLASS = "per_class"                # NEW


_REQUIRED_FIELDS: dict[EventKind, tuple[str, ...]] = {
    EventKind.STAGE_BEGIN: ("stage",),
    EventKind.STAGE_END: ("stage", "status"),
    EventKind.DATA_LOADED: (),
    EventKind.EPOCH_BEGIN: ("epoch",),
    EventKind.EPOCH_END: ("epoch",),
    EventKind.METRIC: ("name", "value"),
    EventKind.ARTIFACT_WRITTEN: ("path",),
    EventKind.CHECKPOINT_SAVED: ("path",),
    EventKind.WARNING: ("message",),
    EventKind.ERROR: ("message",),
    EventKind.CONFUSION_MATRIX: ("labels", "matrix"),  # NEW
    EventKind.PER_CLASS: ("per_class",),               # NEW
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_kinds.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add maldet/events/kinds.py tests/test_event_kinds.py
git commit -m "feat(events): add confusion_matrix and per_class event kinds"
```

---

### Task 1.2: Add `positive_class` field to `OutputConfig`

**Files:**

- Modify: `MALDET_REPO/maldet/manifest.py`
- Test: `MALDET_REPO/tests/test_manifest.py`

- [ ] **Step 1: Write failing tests for positive_class semantics**

```python
# tests/test_manifest.py
import pytest
from pydantic import ValidationError

from maldet.manifest import OutputConfig


def test_positive_class_required_for_binary():
    with pytest.raises(ValidationError, match="positive_class is required"):
        OutputConfig(
            task="binary_classification",
            classes=["Benign", "Malware"],
            score_range=(0.0, 1.0),
        )


def test_positive_class_must_be_in_classes():
    with pytest.raises(ValidationError, match="not in output.classes"):
        OutputConfig(
            task="binary_classification",
            classes=["Benign", "Malware"],
            positive_class="NotARealClass",
            score_range=(0.0, 1.0),
        )


def test_binary_classification_requires_two_classes():
    with pytest.raises(ValidationError, match="exactly 2 classes"):
        OutputConfig(
            task="binary_classification",
            classes=["A", "B", "C"],
            positive_class="A",
            score_range=(0.0, 1.0),
        )


def test_positive_class_optional_for_multiclass():
    cfg = OutputConfig(
        task="multiclass_classification",
        classes=["A", "B", "C"],
        score_range=(0.0, 1.0),
    )
    assert cfg.positive_class is None


def test_binary_with_valid_positive_class():
    cfg = OutputConfig(
        task="binary_classification",
        classes=["Benign", "Malware"],
        positive_class="Malware",
        score_range=(0.0, 1.0),
    )
    assert cfg.positive_class == "Malware"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_manifest.py -v -k positive_class`
Expected: FAIL — field does not exist

- [ ] **Step 3: Add the field + validator**

```python
# maldet/manifest.py — modify OutputConfig
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class OutputConfig(_Frozen):
    task: Literal["binary_classification", "multiclass_classification", "regression", "ranking"]
    classes: list[str] = Field(default_factory=list)
    positive_class: str | None = None
    score_range: tuple[float, float] = (0.0, 1.0)

    @model_validator(mode="after")
    def _validate_positive_class(self) -> Self:
        if self.task == "binary_classification":
            if self.positive_class is None:
                raise ValueError(
                    "output.positive_class is required for binary_classification"
                )
            if self.positive_class not in self.classes:
                raise ValueError(
                    f"output.positive_class={self.positive_class!r} "
                    f"not in output.classes={self.classes!r}"
                )
            if len(self.classes) != 2:
                raise ValueError(
                    f"binary_classification requires exactly 2 classes, "
                    f"got {len(self.classes)}"
                )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_manifest.py -v -k positive_class`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add maldet/manifest.py tests/test_manifest.py
git commit -m "feat(manifest): add explicit OutputConfig.positive_class for binary tasks"
```

---

### Task 1.3: Bump `CompatConfig.schema_version` to 2 and `min_maldet` to "2.0"

**Files:**

- Modify: `MALDET_REPO/maldet/manifest.py`
- Test: `MALDET_REPO/tests/test_manifest.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_manifest.py — add
from maldet.manifest import CompatConfig


def test_schema_version_default_is_2():
    cfg = CompatConfig()
    assert cfg.schema_version == 2


def test_min_maldet_default_is_2_0():
    cfg = CompatConfig()
    assert cfg.min_maldet == "2.0"
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_manifest.py::test_schema_version_default_is_2 tests/test_manifest.py::test_min_maldet_default_is_2_0 -v`
Expected: FAIL (current default is 1 / "1.0")

- [ ] **Step 3: Update defaults**

```python
# maldet/manifest.py — modify CompatConfig
class CompatConfig(_Frozen):
    min_python: str = "3.12"
    min_maldet: str = "2.0"
    schema_version: int = 2
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/manifest.py tests/test_manifest.py
git commit -m "chore(manifest): bump schema_version=2 and min_maldet=2.0"
```

---

### Task 1.4: Update `Trainer.fit` protocol with `classes` kwarg

**Files:**

- Modify: `MALDET_REPO/maldet/protocols.py`

- [ ] **Step 1: Update protocol signature**

```python
# maldet/protocols.py — modify Trainer protocol
from collections.abc import Sequence

@runtime_checkable
class Trainer(Protocol):
    def fit(
        self,
        model: Any,
        train: SampleReader,
        extractor: FeatureExtractor,
        *,
        classes: Sequence[str],            # NEW
        val: SampleReader | None = None,
        logger: EventLogger,
    ) -> TrainResult: ...
    def save(self, result: TrainResult, out_dir: Path) -> None: ...
    def load(self, model_dir: Path) -> Any: ...
```

- [ ] **Step 2: Run existing protocol-conformance tests to confirm baseline**

Run: `uv run pytest tests/ -v -k protocol`
Expected: any existing tests still pass; new signature is purely additive at this point because no implementation has been updated yet (Tasks 1.5/1.6 follow).

- [ ] **Step 3: Commit**

```bash
git add maldet/protocols.py
git commit -m "feat(protocols): add classes kwarg to Trainer.fit"
```

---

### Task 1.5: Update `SklearnTrainer` to use `classes.index(label)` encoding

**Files:**

- Modify: `MALDET_REPO/maldet/trainers/sklearn_trainer.py`
- Test: `MALDET_REPO/tests/test_sklearn_trainer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sklearn_trainer.py
import numpy as np
import pytest

from maldet.trainers.sklearn_trainer import SklearnTrainer
from maldet.types import Sample
from pathlib import Path


class _FakeReader:
    def __init__(self, samples):
        self._samples = samples
    def __iter__(self):
        return iter(self._samples)
    def __len__(self):
        return len(self._samples)


class _FakeExtractor:
    output_shape = (1,)
    dtype = "float32"
    def extract(self, sample):
        return np.array([1.0 if sample.label == "Malware" else 0.0], dtype=np.float32)


class _FakeLogger:
    def __init__(self):
        self.events = []
        self.metrics = []
        self.params = []
    def log_metric(self, name, value, step=None): self.metrics.append((name, value))
    def log_params(self, params): self.params.append(dict(params))
    def log_artifact(self, *a, **kw): pass
    def log_event(self, kind, **payload): self.events.append((kind, payload))
    def set_tags(self, tags): pass


class _FakeModel:
    def __init__(self):
        self.fitted_y = None
    def get_params(self):
        return {"alpha": 0.5}
    def fit(self, X, y):
        self.fitted_y = y.copy()
        return self
    def predict(self, X):
        return np.zeros(len(X), dtype=np.int64)


def _samples(*labels):
    out = []
    for i, lbl in enumerate(labels):
        sha = f"{i:064x}"
        out.append(Sample(sha256=sha, path=Path("/tmp/_x"), label=lbl))
    return out


def test_encoding_uses_classes_index_alphabetical():
    trainer = SklearnTrainer()
    model = _FakeModel()
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    trainer.fit(
        model, _FakeReader(samples), _FakeExtractor(),
        classes=["Benign", "Malware"], logger=_FakeLogger(),
    )
    # classes=["Benign", "Malware"] → Benign=0, Malware=1
    np.testing.assert_array_equal(model.fitted_y, [0, 1, 0, 1])


def test_encoding_uses_classes_index_positive_first():
    trainer = SklearnTrainer()
    model = _FakeModel()
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    trainer.fit(
        model, _FakeReader(samples), _FakeExtractor(),
        classes=["Malware", "Benign"], logger=_FakeLogger(),
    )
    # classes=["Malware", "Benign"] → Malware=0, Benign=1
    np.testing.assert_array_equal(model.fitted_y, [1, 0, 1, 0])


def test_encoding_unknown_label_raises():
    trainer = SklearnTrainer()
    model = _FakeModel()
    samples = _samples("Benign", "AlienClass")
    with pytest.raises(ValueError, match="not in manifest classes"):
        trainer.fit(
            model, _FakeReader(samples), _FakeExtractor(),
            classes=["Benign", "Malware"], logger=_FakeLogger(),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sklearn_trainer.py -v -k encoding`
Expected: FAIL — `fit()` does not accept `classes` kwarg

- [ ] **Step 3: Update `_materialize` and `fit` to accept `classes`**

```python
# maldet/trainers/sklearn_trainer.py
from collections.abc import Sequence


def _materialize(
    reader: SampleReader,
    extractor: FeatureExtractor,
    *,
    classes: Sequence[str] | None,
    require_labels: bool,
) -> tuple[np.ndarray, np.ndarray]:
    class_to_idx = {c: i for i, c in enumerate(classes or [])}
    xs: list[np.ndarray] = []
    ys: list[int] = []
    for sample in reader:
        xs.append(extractor.extract(sample))
        if require_labels:
            if sample.label is None:
                raise ValueError(
                    "SklearnTrainer: reader yielded an unlabeled sample during fit/val"
                )
            if sample.label not in class_to_idx:
                raise ValueError(
                    f"sample.label={sample.label!r} not in manifest classes="
                    f"{list(classes or [])!r}"
                )
            ys.append(class_to_idx[sample.label])
    if not xs:
        raise RuntimeError("SklearnTrainer: reader yielded zero samples")
    X = np.stack(xs)  # noqa: N806
    y = np.asarray(ys, dtype=np.int64) if require_labels else np.empty(0, dtype=np.int64)
    return X, y


class SklearnTrainer:
    def fit(
        self,
        model: Any,
        train: SampleReader,
        extractor: FeatureExtractor,
        *,
        classes: Sequence[str],
        val: SampleReader | None = None,
        logger: EventLogger,
    ) -> TrainResult:
        logger.log_event("stage_begin", stage="train")
        if hasattr(model, "get_params"):
            logger.log_params({k: str(v) for k, v in model.get_params().items()})

        X, y = _materialize(train, extractor, classes=classes, require_labels=True)  # noqa: N806
        logger.log_event("data_loaded", n_train=int(X.shape[0]))

        t0 = time.time()
        model.fit(X, y)
        duration = float(time.time() - t0)
        logger.log_metric("train_time_seconds", duration)

        if val is not None:
            Xv, yv = _materialize(val, extractor, classes=classes, require_labels=True)  # noqa: N806
            acc = float(accuracy_score(yv, model.predict(Xv)))
            logger.log_metric("val_accuracy", acc)

        logger.log_event("stage_end", stage="train", status="success")
        return TrainResult(model=model, extras={"train_time_seconds": duration})

    # save / load unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sklearn_trainer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/trainers/sklearn_trainer.py tests/test_sklearn_trainer.py
git commit -m "feat(trainer): SklearnTrainer encodes labels via classes.index"
```

---

### Task 1.6: Update `LightningTrainer` to use `classes.index(label)` encoding

**Files:**

- Modify: `MALDET_REPO/maldet/trainers/lightning_trainer.py`
- Test: `MALDET_REPO/tests/test_lightning_trainer.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_lightning_trainer.py — encoding-only test (no real Lightning)
import numpy as np
import pytest
import torch
from pathlib import Path

from maldet.trainers.lightning_trainer import _materialize_tensor
from maldet.types import Sample


class _Reader:
    def __init__(self, samples): self._s = samples
    def __iter__(self): return iter(self._s)
    def __len__(self): return len(self._s)


class _Extractor:
    output_shape = (1,)
    dtype = "float32"
    def extract(self, s): return np.array([1.0], dtype=np.float32)


def _samples(*labels):
    return [Sample(sha256=f"{i:064x}", path=Path("/tmp/_x"), label=lbl)
            for i, lbl in enumerate(labels)]


def test_lightning_encoding_alphabetical():
    samples = _samples("Benign", "Malware", "Benign")
    _x, y = _materialize_tensor(_Reader(samples), _Extractor(), classes=["Benign", "Malware"])
    assert y.tolist() == [0, 1, 0]


def test_lightning_encoding_positive_first():
    samples = _samples("Benign", "Malware", "Benign")
    _x, y = _materialize_tensor(_Reader(samples), _Extractor(), classes=["Malware", "Benign"])
    assert y.tolist() == [1, 0, 1]


def test_lightning_encoding_unknown_label_raises():
    samples = _samples("Benign", "Outlier")
    with pytest.raises(ValueError, match="not in manifest classes"):
        _materialize_tensor(_Reader(samples), _Extractor(), classes=["Benign", "Malware"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lightning_trainer.py -v`
Expected: FAIL — `_materialize_tensor` does not accept `classes` kwarg

- [ ] **Step 3: Update `_materialize_tensor` and `fit` signatures**

```python
# maldet/trainers/lightning_trainer.py
from collections.abc import Sequence


def _materialize_tensor(
    reader: SampleReader,
    extractor: FeatureExtractor,
    *,
    classes: Sequence[str],
) -> tuple[torch.Tensor, torch.Tensor]:
    class_to_idx = {c: i for i, c in enumerate(classes)}
    xs: list[np.ndarray] = []
    ys: list[int] = []
    for sample in reader:
        xs.append(extractor.extract(sample))
        if sample.label is None:
            raise ValueError("LightningTrainer: unlabeled sample encountered during fit")
        if sample.label not in class_to_idx:
            raise ValueError(
                f"sample.label={sample.label!r} not in manifest classes={list(classes)!r}"
            )
        ys.append(class_to_idx[sample.label])
    if not xs:
        raise RuntimeError("LightningTrainer: reader yielded zero samples")
    X = np.stack(xs)  # noqa: N806
    if str(X.dtype) == "uint8":
        x_t = torch.from_numpy(X.astype(np.int64))
    else:
        x_t = torch.from_numpy(X.astype(np.float32))
    y_t = torch.tensor(ys, dtype=torch.int64)
    return x_t, y_t


class LightningTrainer:
    def fit(
        self,
        model: Any,
        train: SampleReader,
        extractor: FeatureExtractor,
        *,
        classes: Sequence[str],
        val: SampleReader | None = None,
        logger: EventLogger,
    ) -> TrainResult:
        # ... existing body, but pass classes= into _materialize_tensor
        X, y = _materialize_tensor(train, extractor, classes=classes)  # noqa: N806
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lightning_trainer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/trainers/lightning_trainer.py tests/test_lightning_trainer.py
git commit -m "feat(trainer): LightningTrainer encodes labels via classes.index"
```

---

### Task 1.7: Update `BinaryClassification.evaluate` — fix CM labels orientation — **SKIPPED (already merged)**

**Files:**

- Modify: `MALDET_REPO/maldet/evaluators/binary.py`
- Test: `MALDET_REPO/tests/test_binary_evaluator.py`

- [ ] **Step 1: Write failing test for CM labels match matrix orientation**

```python
# tests/test_binary_evaluator.py
import numpy as np
import pytest
from pathlib import Path

from maldet.evaluators.binary import BinaryClassification
from maldet.types import Sample


class _Reader:
    def __init__(self, samples): self._s = samples
    def __iter__(self): return iter(self._s)
    def __len__(self): return len(self._s)


class _Extractor:
    output_shape = (1,)
    dtype = "float32"
    def extract(self, s):
        return np.array([1.0 if s.label == "Malware" else 0.0], dtype=np.float32)


class _Logger:
    def __init__(self):
        self.events = []
        self.metrics = []
    def log_metric(self, n, v, step=None): self.metrics.append((n, v))
    def log_params(self, p): pass
    def log_artifact(self, *a, **kw): pass
    def log_event(self, k, **p): self.events.append((k, p))
    def set_tags(self, t): pass


class _PerfectModel:
    """Returns 1 if first feature == 1.0 (Malware), else 0."""
    def predict(self, X):
        return np.asarray((X[:, 0] > 0.5).astype(np.int64))


def _samples(*labels):
    return [Sample(sha256=f"{i:064x}", path=Path("/tmp/_x"), label=lbl)
            for i, lbl in enumerate(labels)]


def test_confusion_matrix_labels_match_matrix_orientation_alphabetical():
    """CM labels list and matrix rows MUST be in the same order (regression
    against maldet 1.x bug where labels=[positive, other] but matrix was
    computed with labels=[0, 1])."""
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    eval_ = BinaryClassification(
        positive_class="Malware",
        class_names=["Benign", "Malware"],
    )
    report = eval_.evaluate(_PerfectModel(), _Reader(samples), _Extractor(), logger=_Logger())
    cm = report.confusion_matrix
    assert cm["labels"] == ["Benign", "Malware"]
    # Perfect model: 2 Benign correct, 2 Malware correct → diagonal
    assert cm["matrix"] == [[2, 0], [0, 2]]


def test_confusion_matrix_labels_match_matrix_orientation_positive_first():
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    eval_ = BinaryClassification(
        positive_class="Malware",
        class_names=["Malware", "Benign"],
    )
    report = eval_.evaluate(_PerfectModel(), _Reader(samples), _Extractor(), logger=_Logger())
    cm = report.confusion_matrix
    # When classes=["Malware", "Benign"], labels=[Malware, Benign] and matrix
    # row 0 = Malware actuals
    assert cm["labels"] == ["Malware", "Benign"]
    assert cm["matrix"] == [[2, 0], [0, 2]]
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_binary_evaluator.py -v -k orientation`
Expected: FAIL (current maldet 1.x writes labels in [positive, other] but matrix in [0,1] order)

- [ ] **Step 3: Update `evaluate` to align labels with matrix**

```python
# maldet/evaluators/binary.py — replace evaluate body
class BinaryClassification:
    def __init__(self, positive_class: str, class_names: Sequence[str]) -> None:
        if positive_class not in class_names:
            raise ValueError(
                f"positive_class {positive_class!r} not in class_names {list(class_names)!r}"
            )
        self._positive = positive_class
        self._classes = list(class_names)
        self._pos_idx = self._classes.index(positive_class)

    def evaluate(self, model, reader, extractor, *, logger):
        t0 = time.time()
        shas: list[str] = []
        ys: list[int] = []
        mats: list[np.ndarray] = []
        class_to_idx = {c: i for i, c in enumerate(self._classes)}
        for sample in reader:
            if sample.label is None:
                raise ValueError(
                    "BinaryClassification.evaluate requires labeled samples"
                )
            shas.append(sample.sha256)
            ys.append(class_to_idx[sample.label])
            mats.append(extractor.extract(sample))
        features = np.stack(mats)
        y = np.asarray(ys)
        y_pred = np.asarray(model.predict(features))

        metrics: dict[str, float] = {
            "accuracy": float(accuracy_score(y, y_pred)),
            "precision": float(precision_score(y, y_pred, pos_label=self._pos_idx, zero_division=0)),
            "recall": float(recall_score(y, y_pred, pos_label=self._pos_idx, zero_division=0)),
            "f1": float(f1_score(y, y_pred, pos_label=self._pos_idx, zero_division=0)),
        }
        proba = getattr(model, "predict_proba", None)
        if callable(proba):
            probs = np.asarray(proba(features))[:, self._pos_idx]
            with contextlib.suppress(ValueError):
                metrics["roc_auc"] = float(roc_auc_score((y == self._pos_idx).astype(int), probs))

        labels_idx = list(range(len(self._classes)))
        cm = confusion_matrix(y, y_pred, labels=labels_idx).tolist()
        cm_payload = {"labels": list(self._classes), "matrix": cm}

        p_per, r_per, f_per, s_per = precision_recall_fscore_support(
            y, y_pred, labels=labels_idx, zero_division=0
        )
        per_class = {
            self._classes[i]: {
                "precision": float(p_per[i]),
                "recall": float(r_per[i]),
                "f1": float(f_per[i]),
                "support": int(s_per[i]),
            }
            for i in range(len(self._classes))
        }

        report = MetricReport(
            task="binary_classification",
            n_samples=len(y),
            duration_seconds=float(time.time() - t0),
            metrics=metrics,
            per_class=per_class,
            confusion_matrix=cm_payload,
        )
        for k, v in metrics.items():
            logger.log_metric(k, v)
        return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_binary_evaluator.py -v -k orientation`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/evaluators/binary.py tests/test_binary_evaluator.py
git commit -m "fix(evaluator): align confusion_matrix labels with matrix row/col order"
```

---

### Task 1.8: Emit `confusion_matrix` and `per_class` events from evaluator — **SKIPPED (already merged)**

**Files:**

- Modify: `MALDET_REPO/maldet/evaluators/binary.py`
- Test: `MALDET_REPO/tests/test_binary_evaluator.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_binary_evaluator.py — add
def test_emits_confusion_matrix_event():
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    logger = _Logger()
    eval_ = BinaryClassification(positive_class="Malware", class_names=["Benign", "Malware"])
    eval_.evaluate(_PerfectModel(), _Reader(samples), _Extractor(), logger=logger)
    cm_events = [e for e in logger.events if e[0] == "confusion_matrix"]
    assert len(cm_events) == 1
    payload = cm_events[0][1]
    assert payload["labels"] == ["Benign", "Malware"]
    assert payload["matrix"] == [[2, 0], [0, 2]]


def test_emits_per_class_event():
    samples = _samples("Benign", "Malware", "Benign", "Malware")
    logger = _Logger()
    eval_ = BinaryClassification(positive_class="Malware", class_names=["Benign", "Malware"])
    eval_.evaluate(_PerfectModel(), _Reader(samples), _Extractor(), logger=logger)
    pc_events = [e for e in logger.events if e[0] == "per_class"]
    assert len(pc_events) == 1
    pc = pc_events[0][1]["per_class"]
    assert "Malware" in pc and "Benign" in pc
    assert pc["Malware"]["support"] == 2
    assert pc["Benign"]["support"] == 2
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_binary_evaluator.py -v -k emits`
Expected: FAIL (no `log_event` calls for these kinds)

- [ ] **Step 3: Add the two `log_event` calls just before returning the report**

```python
# maldet/evaluators/binary.py — extend the tail of evaluate()
        for k, v in metrics.items():
            logger.log_metric(k, v)
        # NEW: emit so reconciler projection sees them
        logger.log_event(
            "confusion_matrix",
            labels=cm_payload["labels"],
            matrix=cm_payload["matrix"],
        )
        logger.log_event("per_class", per_class=per_class)
        return report
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_binary_evaluator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/evaluators/binary.py tests/test_binary_evaluator.py
git commit -m "feat(evaluator): emit confusion_matrix and per_class events"
```

---

### Task 1.9: Update `runner.py` — pass `classes` to trainer, `positive_class` from manifest

**Files:**

- Modify: `MALDET_REPO/maldet/runner.py`
- Test: `MALDET_REPO/tests/test_runner.py` (extend or new)

- [ ] **Step 1: Write failing test**

```python
# tests/test_runner.py — assumes existing runner test scaffolding
def test_train_passes_classes_to_trainer(tmp_path, monkeypatch):
    # mock manifest + scaffolding to assert that StageRunner.run(stage="train", ...)
    # invokes Trainer.fit with classes=manifest.output.classes
    captured = {}

    class _RecordingTrainer:
        def fit(self, model, train, extractor, *, classes, val=None, logger):
            captured["classes"] = list(classes)
            from maldet.types import TrainResult
            return TrainResult(model=model, extras={})
        def save(self, *a, **kw): pass
        def load(self, *a, **kw): return None

    # ... wire in via the runner's import-resolution mechanism
    # (test specifics depend on existing harness)
    assert captured["classes"] == ["Benign", "Malware"]


def test_evaluate_passes_positive_class_from_manifest():
    # similarly assert StageRunner instantiates BinaryClassification(
    #   positive_class=manifest.output.positive_class, ...)
    pass
```

(Concrete wiring depends on the maldet repo's existing runner-test harness — adapt to match.)

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_runner.py -v -k passes_classes`
Expected: FAIL — current runner doesn't pass `classes`

- [ ] **Step 3: Update runner**

```python
# maldet/runner.py — modify the train/evaluate branches
        if stage == "train":
            train_csv = Path(str(cfg.data.train_csv))
            samples_root = Path(str(cfg.paths.samples_root))
            reader = reader_cls(csv=train_csv, samples_root=samples_root)
            extractor = extractor_cls()

            model = hydra_instantiate(cfg.model, _convert_="partial")
            trainer_cls = _load_symbol(_require(stage_spec.trainer, "trainer"))
            trainer = trainer_cls()
            result = trainer.fit(
                model, reader, extractor,
                classes=self._manifest.output.classes,
                logger=logger,
            )
            trainer.save(result, output_dir / "model")
            return

        if stage == "evaluate":
            source_model = Path(str(cfg.paths.source_model))
            train_spec = self._manifest.stages.get("train")
            trainer_symbol = stage_spec.trainer or (train_spec.trainer if train_spec else None)
            trainer = _load_symbol(_require(trainer_symbol, "trainer"))()
            model = trainer.load(source_model)
            test_csv = Path(str(cfg.data.test_csv))
            samples_root = Path(str(cfg.paths.samples_root))
            reader = reader_cls(csv=test_csv, samples_root=samples_root)
            extractor = extractor_cls()
            evaluator_cls = _load_symbol(_require(stage_spec.evaluator, "evaluator"))
            # NEW: positive_class from manifest, not classes[0]
            evaluator = evaluator_cls(
                positive_class=self._manifest.output.positive_class,
                class_names=self._manifest.output.classes,
            )
            report = evaluator.evaluate(model, reader, extractor, logger=logger)
            (output_dir / "metrics.json").write_text(
                json.dumps(report.to_json_dict(), indent=2, default=str), encoding="utf-8"
            )
            return
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS (previous tests + new runner tests)

- [ ] **Step 5: Commit**

```bash
git add maldet/runner.py tests/test_runner.py
git commit -m "feat(runner): pass classes to trainer and explicit positive_class to evaluator"
```

---

### Task 1.10: Bump maldet version to 2.0.0 and write CHANGELOG

**Files:**

- Modify: `MALDET_REPO/maldet/_version.py`
- Modify: `MALDET_REPO/pyproject.toml`
- Modify: `MALDET_REPO/CHANGELOG.md` (create if absent)

- [ ] **Step 1: Update version constant**

```python
# maldet/_version.py
__version__ = "2.0.0"
```

- [ ] **Step 2: Update pyproject**

```toml
# pyproject.toml — only the version line
[project]
name = "maldet"
version = "2.0.0"
```

- [ ] **Step 3: Write CHANGELOG entry**

````markdown
# Changelog

## 2.0.0 — 2026-05-02

**BREAKING CHANGES**

- `OutputConfig.positive_class` is now **required** for `binary_classification` task. Add it to your `maldet.toml` manifest.
- `CompatConfig.schema_version` bumped to `2`. Manifests with `schema_version < 2` are rejected.
- `CompatConfig.min_maldet` default is now `"2.0"`.
- `Trainer.fit` protocol gains a required `classes: Sequence[str]` keyword argument. Custom trainer subclasses must update their signatures.
- `SklearnTrainer` and `LightningTrainer` no longer hard-code "Malware" as label 1. Encoding is `classes.index(sample.label)`. **Models trained with maldet 1.x must be retrained.**
- `BinaryClassification` evaluator now emits `confusion_matrix` and `per_class` events via `logger.log_event`.
- `BinaryClassification` confusion-matrix `labels` order now matches the matrix row/col order (was inverted in 1.x).

**MIGRATION**

```toml
# Add to maldet.toml under [output]
positive_class = "Malware"     # the class you care about (sklearn pos_label)

# Bump compat
[compat]
schema_version = 2
min_maldet = "2.0"
```
````

````

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add maldet/_version.py pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump version to 2.0.0"
````

---

### Task 1.11: Tag and publish maldet 2.0.0 to PyPI

**Files:**

- (no source changes; release operation only)

- [ ] **Step 1: Create annotated tag**

```bash
git tag -a v2.0.0 -m "maldet 2.0.0 — explicit positive_class, fixed CM, evaluator events"
git push origin main --follow-tags
```

- [ ] **Step 2: Build the distribution**

```bash
uv build
# produces dist/maldet-2.0.0-py3-none-any.whl and dist/maldet-2.0.0.tar.gz
```

- [ ] **Step 3: Publish to PyPI**

```bash
uv publish dist/maldet-2.0.0*
# (or your repo's publish flow — twine, GH Action, etc.)
```

- [ ] **Step 4: Verify install from PyPI**

```bash
mkdir /tmp/verify-maldet-2 && cd /tmp/verify-maldet-2
python -m venv .venv && source .venv/bin/activate
pip install maldet==2.0.0
python -c "import maldet; print(maldet.__version__)"
# Expected: 2.0.0
```

- [ ] **Step 5: Stage Phase 2 dependency bump**

Phase 1 is now complete from PyPI's perspective. Move to `LOLDAY_REPO`. No commit here.

---

# Phase 2 — Lolday backend

> All Phase 2 tasks run in `LOLDAY_REPO`. Use `cd backend && uv run pytest` per repo convention.

### Task 2.1: Bump maldet dependency in backend + build-helper

**Files:**

- Modify: `LOLDAY_REPO/backend/pyproject.toml`
- Modify: `LOLDAY_REPO/backend/uv.lock` (regenerated)
- Modify: `LOLDAY_REPO/charts/lolday/helpers/build-helper/pyproject.toml`
- Modify: `LOLDAY_REPO/charts/lolday/helpers/build-helper/uv.lock` (regenerated)

- [ ] **Step 1: Update backend pyproject**

```toml
# backend/pyproject.toml — find the maldet line and change
"maldet>=2.0,<3",
```

- [ ] **Step 2: Update build-helper pyproject**

```toml
# charts/lolday/helpers/build-helper/pyproject.toml
"maldet[lightning]>=2.0,<3.0",
```

- [ ] **Step 3: Regenerate lockfiles**

```bash
cd backend && uv lock
cd ../charts/lolday/helpers/build-helper && uv lock
cd ../../../..
```

- [ ] **Step 4: Smoke import**

Run: `cd backend && uv run python -c "import maldet; print(maldet.__version__)"`
Expected: `2.0.0`

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock \
  charts/lolday/helpers/build-helper/pyproject.toml \
  charts/lolday/helpers/build-helper/uv.lock
git commit -m "chore(deps): bump maldet to 2.x"
```

---

### Task 2.2: Update test fixtures with `positive_class`

**Files:**

- Modify: `LOLDAY_REPO/backend/tests/conftest.py`
- Modify: `LOLDAY_REPO/backend/tests/test_services_validator.py`
- Modify: `LOLDAY_REPO/backend/tests/test_services_validator_phase11b.py`
- Modify: `LOLDAY_REPO/backend/tests/test_routers_jobs.py`

- [ ] **Step 1: Run the existing test suite to capture failures from the bump**

Run: `cd backend && uv run pytest tests/ -x -v 2>&1 | head -60`
Expected: failures referencing missing `positive_class` (Pydantic validation errors triggered by maldet 2.0)

- [ ] **Step 2: Add `positive_class` to each fixture**

In each affected file, locate every literal dict containing `"task": "binary_classification"` and `"classes": ["Malware", "Benign"]` (or similar) and patch:

```python
# Example pattern, applied to all 7 occurrences across 4 files:
{
    "task": "binary_classification",
    "classes": ["Benign", "Malware"],     # normalised alphabetical
    "positive_class": "Malware",          # NEW
    "score_range": [0.0, 1.0],
}
```

Note: changing classes ordering from `["Malware", "Benign"]` to `["Benign", "Malware"]` is cosmetic. The test assertions that check class names by string still pass.

Files and line hints (verify with `git grep -n '"classes":' backend/tests/`):

- `backend/tests/conftest.py:428`
- `backend/tests/test_services_validator.py` (4 occurrences)
- `backend/tests/test_services_validator_phase11b.py:24`
- `backend/tests/test_routers_jobs.py` (2 occurrences)

- [ ] **Step 3: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/ -v`
Expected: ALL PASS (or unrelated failures only)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/conftest.py \
  backend/tests/test_services_validator.py \
  backend/tests/test_services_validator_phase11b.py \
  backend/tests/test_routers_jobs.py
git commit -m "test(backend): add positive_class to manifest fixtures for maldet 2.0"
```

---

### Task 2.3: Add RFC 6266 `Content-Disposition` to `download_artifact`

**Files:**

- Modify: `LOLDAY_REPO/backend/app/routers/experiments_proxy.py:153-174`
- Test: `LOLDAY_REPO/backend/tests/test_routers_experiments_proxy.py` (new file)

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_routers_experiments_proxy.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_download_artifact_sets_content_disposition_ascii(
    authed_client: AsyncClient, mlflow_run_with_artifact
):
    run_id = mlflow_run_with_artifact["run_id"]
    resp = await authed_client.get(
        f"/api/v1/runs/{run_id}/artifacts/download?path=predictions.csv"
    )
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert 'filename="predictions.csv"' in cd
    assert "filename*=UTF-8''predictions.csv" in cd


@pytest.mark.asyncio
async def test_download_artifact_unicode_filename(
    authed_client: AsyncClient, mlflow_run_with_unicode_artifact
):
    run_id = mlflow_run_with_unicode_artifact["run_id"]
    # path includes "混淆樣本.csv"
    from urllib.parse import quote
    encoded_path = quote("混淆樣本.csv", safe="")
    resp = await authed_client.get(
        f"/api/v1/runs/{run_id}/artifacts/download?path={encoded_path}"
    )
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    # ASCII fallback present and uses underscore for non-ASCII
    assert 'filename="' in cd
    # RFC 5987 percent-encoded UTF-8 form present
    assert "filename*=UTF-8''" in cd


@pytest.mark.asyncio
async def test_download_artifact_strips_quote_from_filename(
    authed_client: AsyncClient, mlflow_run_with_quote_filename_artifact
):
    """A path basename containing `"` must not break the header by injection."""
    run_id = mlflow_run_with_quote_filename_artifact["run_id"]
    resp = await authed_client.get(
        f'/api/v1/runs/{run_id}/artifacts/download?path=foo%22bar.csv'
    )
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    # ASCII fallback replaces " with _
    assert 'filename="foo_bar.csv"' in cd
```

(If existing fixtures `mlflow_run_with_artifact` etc. don't exist, write minimal ones in `conftest.py` mocking `MlflowClient.get_run` and `httpx.AsyncClient.get` to return canned responses.)

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/test_routers_experiments_proxy.py -v`
Expected: FAIL — header missing or wrong

- [ ] **Step 3: Update `download_artifact`**

```python
# backend/app/routers/experiments_proxy.py
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import quote


def _build_content_disposition(filename: str) -> str:
    """RFC 6266: ``attachment; filename="<ascii>"; filename*=UTF-8''<percent-encoded>``."""
    ascii_fallback = (
        filename.encode("ascii", errors="replace")
        .decode("ascii")
        .replace('"', "_")
    )
    quoted = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'


@router.get("/runs/{run_id}/artifacts/download")
async def download_artifact(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    run = await _client().get_run(run_id)
    artifact_uri: str = run["info"]["artifact_uri"]
    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise HTTPException(
            status_code=502,
            detail=f"unexpected artifact_uri scheme: {artifact_uri!r}",
        )
    relative = artifact_uri[len(prefix):].rstrip("/")
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/{relative}/{path}"
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)

    filename = PurePosixPath(path).name or "artifact"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=r.content,
        media_type=media_type,
        headers={"Content-Disposition": _build_content_disposition(filename)},
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_routers_experiments_proxy.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/experiments_proxy.py \
  backend/tests/test_routers_experiments_proxy.py
git commit -m "fix(experiments_proxy): set RFC 6266 Content-Disposition on download"
```

---

### Task 2.4: Add `BACKEND_MAINTENANCE_MODE` flag and gate `POST /jobs`

**Files:**

- Modify: `LOLDAY_REPO/backend/app/config.py`
- Modify: `LOLDAY_REPO/backend/app/routers/jobs.py`
- Test: `LOLDAY_REPO/backend/tests/test_routers_jobs.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_routers_jobs.py — add
@pytest.mark.asyncio
async def test_create_job_blocked_in_maintenance_mode(
    authed_client, monkeypatch, valid_job_payload
):
    from app.config import settings
    monkeypatch.setattr(settings, "BACKEND_MAINTENANCE_MODE", True)
    resp = await authed_client.post("/api/v1/jobs", json=valid_job_payload)
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    body = resp.json()
    assert body.get("detail", "").lower().startswith("maintenance")


@pytest.mark.asyncio
async def test_create_job_allowed_when_maintenance_off(
    authed_client, monkeypatch, valid_job_payload
):
    from app.config import settings
    monkeypatch.setattr(settings, "BACKEND_MAINTENANCE_MODE", False)
    resp = await authed_client.post("/api/v1/jobs", json=valid_job_payload)
    assert resp.status_code in (200, 201)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/test_routers_jobs.py -v -k maintenance`
Expected: FAIL — setting does not exist; route does not check it

- [ ] **Step 3: Add the setting**

```python
# backend/app/config.py — inside Settings class
BACKEND_MAINTENANCE_MODE: bool = False
```

- [ ] **Step 4: Add the gate at the head of `POST /jobs`**

```python
# backend/app/routers/jobs.py — at the top of create_job (or main POST handler)
from fastapi import Response

@router.post(...)
async def create_job(
    body: JobCreate,
    response: Response,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    if settings.BACKEND_MAINTENANCE_MODE:
        raise HTTPException(
            status_code=503,
            detail="maintenance: platform under maintenance, try again later",
            headers={"Retry-After": "3600"},
        )
    # ... existing body
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_routers_jobs.py -v -k maintenance`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/routers/jobs.py \
  backend/tests/test_routers_jobs.py
git commit -m "feat(jobs): BACKEND_MAINTENANCE_MODE gates new submissions with 503"
```

---

# Phase 3 — Lolday frontend

> All Phase 3 tasks run in `LOLDAY_REPO`. Use `cd frontend && pnpm test` for vitest, `pnpm playwright test` for e2e.

### Task 3.1: Replace Run Detail page with redirect logic

**Files:**

- Replace: `LOLDAY_REPO/frontend/src/routes/_authed.runs.$expId.$runId.tsx`
- Test: `LOLDAY_REPO/frontend/src/routes/_authed.runs.$expId.$runId.test.tsx` (new)

- [ ] **Step 1: Write failing tests**

```tsx
// frontend/src/routes/_authed.runs.$expId.$runId.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import RunRedirectPage from "./_authed.runs.$expId.$runId";

vi.mock("@/api/queries/runs", () => ({
  useRun: vi.fn(),
}));

const wrap = (initial: string) => {
  const qc = new QueryClient();
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/runs/:expId/:runId" element={<RunRedirectPage />} />
          <Route path="/jobs/:id" element={<div data-testid="jobs-page" />} />
          <Route path="/runs" element={<div data-testid="runs-index" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("RunRedirectPage", () => {
  it("redirects to /jobs/<jobId> when run has lolday.job_id tag", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as any).mockReturnValue({
      data: { tags: { "lolday.job_id": "abc-123" } },
      isLoading: false,
      error: null,
    });
    render(wrap("/runs/exp1/run1"));
    await waitFor(() => screen.getByTestId("jobs-page"));
  });

  it("redirects to runs index on error", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as any).mockReturnValue({
      data: null,
      isLoading: false,
      error: new Error("404"),
    });
    render(wrap("/runs/exp1/run1"));
    await waitFor(() => screen.getByTestId("runs-index"));
  });

  it("shows loading state during fetch", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as any).mockReturnValue({
      data: null,
      isLoading: true,
      error: null,
    });
    render(wrap("/runs/exp1/run1"));
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd frontend && pnpm vitest src/routes/_authed.runs.\$expId.\$runId.test.tsx --run`
Expected: FAIL — current Run Detail page renders MetricsTable etc., not redirects

- [ ] **Step 3: Replace the route file**

```tsx
// frontend/src/routes/_authed.runs.$expId.$runId.tsx
import { useEffect } from "react";
import { Navigate, useParams } from "react-router";
import { useRun } from "@/api/queries/runs";

export const handle = { breadcrumb: "Run" };

export default function RunRedirectPage() {
  const { expId = "", runId = "" } = useParams();
  const { data, isLoading, error } = useRun(runId);

  // External redirect for orphan runs (no lolday.job_id tag)
  const run = data as { tags?: Record<string, string> } | null;
  const jobId =
    run?.tags?.["lolday.job_id"] ?? run?.tags?.lolday_job_id ?? null;
  const orphan = data && !jobId;
  useEffect(() => {
    if (orphan) {
      window.location.replace(`/mlflow/#/experiments/${expId}/runs/${runId}`);
    }
  }, [orphan, expId, runId]);

  if (isLoading) {
    return <p className="text-muted-foreground">Loading…</p>;
  }
  if (error || !data) {
    return <Navigate to="/runs" replace />;
  }
  if (jobId) {
    return <Navigate to={`/jobs/${jobId}`} replace />;
  }
  return <p className="text-muted-foreground">Redirecting to MLflow…</p>;
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend && pnpm vitest src/routes/_authed.runs.\$expId.\$runId.test.tsx --run`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.runs.\$expId.\$runId.tsx \
  frontend/src/routes/_authed.runs.\$expId.\$runId.test.tsx
git commit -m "refactor(runs): replace Run Detail with redirect to Job Detail or MLflow"
```

---

### Task 3.2: Remove "Open run ↗" tab from Job Detail

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/routes/_authed.jobs.$id.tsx`
- Test: `LOLDAY_REPO/frontend/src/routes/_authed.jobs.$id.test.tsx` (new or extend)

- [ ] **Step 1: Write failing test**

```tsx
// frontend/src/routes/_authed.jobs.$id.test.tsx (add or new file)
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/queries/jobs", () => ({
  useJob: () => ({
    data: {
      id: "j1",
      type: "train",
      status: "succeeded",
      mlflow_run_id: "r1",
      mlflow_experiment_id: "e1",
    },
  }),
  useJobLogs: () => ({ data: "" }),
}));

import JobDetailPage from "./_authed.jobs.$id";

describe("JobDetailPage tabs", () => {
  it("does not render an Open run tab", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/jobs/j1"]}>
          <JobDetailPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByText(/open run/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd frontend && pnpm vitest src/routes/_authed.jobs.\$id.test.tsx --run`
Expected: FAIL — tab is currently rendered

- [ ] **Step 3: Remove the Open-run tab block**

```tsx
// frontend/src/routes/_authed.jobs.$id.tsx — DELETE this block
{
  /* before */
}
{
  job.mlflow_run_id && (
    <TabsTrigger value="mlflow" asChild>
      <Link to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}>
        Open run ↗
      </Link>
    </TabsTrigger>
  );
}
```

After deletion, TabsList contains: Summary, Logs, Artifacts.

- [ ] **Step 4: Run test to verify pass**

Run: `cd frontend && pnpm vitest src/routes/_authed.jobs.\$id.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.jobs.\$id.tsx \
  frontend/src/routes/_authed.jobs.\$id.test.tsx
git commit -m "refactor(jobs): remove redundant Open run tab from Job Detail"
```

---

### Task 3.3: Add `OpenInMlflowButton` to Job Detail header

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/components/jobs/JobDetailShell.tsx`
- Test: `LOLDAY_REPO/frontend/src/components/jobs/JobDetailShell.test.tsx` (new or extend)

- [ ] **Step 1: Write failing test**

```tsx
// frontend/src/components/jobs/JobDetailShell.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { JobDetailShell } from "./JobDetailShell";

vi.mock("@/api/queries/cluster", () => ({
  useJobQueuePosition: () => ({ data: null }),
}));
vi.mock("@/api/queries/jobs", () => ({
  useCancelJob: () => ({ mutate: vi.fn() }),
}));

const renderShell = (overrides: Record<string, any> = {}) => {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <JobDetailShell
          job={
            {
              id: "j-1",
              type: "train",
              status: "succeeded",
              mlflow_run_id: "r-1",
              mlflow_experiment_id: "e-1",
              submitted_at: new Date().toISOString(),
              started_at: new Date().toISOString(),
              finished_at: new Date().toISOString(),
              failure_reason: null,
              ...overrides,
            } as any
          }
        >
          <div />
        </JobDetailShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("JobDetailShell", () => {
  it("renders Open in MLflow when run id and experiment id are set", () => {
    renderShell();
    expect(screen.getByText(/open in mlflow/i)).toBeInTheDocument();
  });

  it("does not render Open in MLflow when run id is missing", () => {
    renderShell({ mlflow_run_id: null });
    expect(screen.queryByText(/open in mlflow/i)).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd frontend && pnpm vitest src/components/jobs/JobDetailShell.test.tsx --run`
Expected: FAIL — button not rendered

- [ ] **Step 3: Add the button to header actions**

```tsx
// frontend/src/components/jobs/JobDetailShell.tsx
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";

// inside the action bar (replace the existing flex gap-2 div):
<div className="flex gap-2">
  {job.mlflow_run_id && job.mlflow_experiment_id && (
    <OpenInMlflowButton
      experimentId={job.mlflow_experiment_id}
      runId={job.mlflow_run_id}
    />
  )}
  <Button variant="ghost" onClick={() => nav(`/jobs/new?from=${job.id}`)}>
    Clone
  </Button>
  {!isTerminal(job.status) && (
    <Button variant="destructive" onClick={() => cancel.mutate(job.id)}>
      Cancel
    </Button>
  )}
</div>;
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend && pnpm vitest src/components/jobs/JobDetailShell.test.tsx --run`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/jobs/JobDetailShell.tsx \
  frontend/src/components/jobs/JobDetailShell.test.tsx
git commit -m "feat(jobs): Job Detail header gains Open in MLflow button"
```

---

### Task 3.4: Rewrite Runs list `run_id` cell + remove redundant Job column

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/routes/_authed.runs.$expId.tsx`
- Test: `LOLDAY_REPO/frontend/src/routes/_authed.runs.$expId.test.tsx` (new or extend)

- [ ] **Step 1: Write failing test**

```tsx
// frontend/src/routes/_authed.runs.$expId.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/queries/runs", () => ({
  useExperimentRuns: () => ({
    data: [
      {
        run_id: "lolday-run",
        run_name: "lolday-run",
        status: "FINISHED",
        tags: { "lolday.job_id": "job-A" },
      },
      {
        run_id: "orphan-run",
        run_name: "orphan-run",
        status: "FINISHED",
        tags: {},
      },
    ],
    isLoading: false,
  }),
}));

import RunsListPage from "./_authed.runs.$expId";

const wrap = () => {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/runs/exp1"]}>
        <RunsListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("RunsListPage cell linking", () => {
  it("renders an internal Link to /jobs/<id> when row has lolday.job_id tag", () => {
    wrap();
    const a = screen.getByText(/lolday-run/i).closest("a")!;
    expect(a.getAttribute("href")).toBe("/jobs/job-A");
  });

  it("renders external link to MLflow when row has no lolday.job_id tag", () => {
    wrap();
    const a = screen.getByText(/orphan-run/i).closest("a")!;
    expect(a.getAttribute("href")).toContain(
      "/mlflow/#/experiments/exp1/runs/orphan-run",
    );
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("does not render a separate Job column", () => {
    wrap();
    expect(screen.queryByRole("columnheader", { name: /^job$/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd frontend && pnpm vitest src/routes/_authed.runs.\$expId.test.tsx --run`
Expected: FAIL

- [ ] **Step 3: Update the columns array**

```tsx
// frontend/src/routes/_authed.runs.$expId.tsx — rewrite columns
const columns: ColumnDef<Row>[] = [
  {
    accessorKey: "run_id",
    header: "Run",
    cell: ({ row }) => {
      const jobId =
        row.original.tags?.["lolday.job_id"] ??
        row.original.tags?.lolday_job_id;
      if (jobId) {
        return (
          <Link
            to={`/jobs/${jobId}`}
            className="font-mono text-sm hover:underline"
          >
            {row.original.run_id.slice(0, 10)}
          </Link>
        );
      }
      return (
        <a
          href={`/mlflow/#/experiments/${expId}/runs/${row.original.run_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-sm hover:underline"
        >
          {row.original.run_id.slice(0, 10)} ↗
        </a>
      );
    },
  },
  { accessorKey: "run_name", header: "Name" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => (
      <StatusBadge status={row.original.status.toLowerCase()} />
    ),
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      row.original.start_time && row.original.end_time
        ? formatDuration(
            new Date(row.original.start_time).toISOString(),
            new Date(row.original.end_time).toISOString(),
          )
        : "—",
  },
  ...selectedCols.map((key): ColumnDef<Row> => {
    const [kind, name] = key.split(".", 2);
    return {
      id: key,
      header: name,
      cell: ({ row }) => {
        const v = pickValue(row.original, kind, name);
        if (typeof v === "number") return v.toFixed(4);
        if (v == null) return "—";
        return String(v);
      },
    };
  }),
  // The previous trailing "Job" column with the ↗ glyph is removed (run column above already does this).
];
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend && pnpm vitest src/routes/_authed.runs.\$expId.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.runs.\$expId.tsx \
  frontend/src/routes/_authed.runs.\$expId.test.tsx
git commit -m "refactor(runs): inline run-cell link routing; drop redundant Job column"
```

---

### Task 3.5: Add `download` attribute to ArtifactTree links

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/components/common/ArtifactTree.tsx`
- Test: `LOLDAY_REPO/frontend/src/components/common/ArtifactTree.test.tsx` (new or extend)

- [ ] **Step 1: Write failing test**

```tsx
// frontend/src/components/common/ArtifactTree.test.tsx
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ArtifactTree } from "./ArtifactTree";

vi.mock("@/api/client", () => ({
  client: {
    GET: vi.fn().mockResolvedValue({
      data: {
        files: [{ path: "predictions.csv", is_dir: false, file_size: 100 }],
      },
      error: null,
    }),
  },
}));

describe("ArtifactTree download attribute", () => {
  it("sets the download attribute to the artifact basename", async () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <ArtifactTree runId="r1" />
      </QueryClientProvider>,
    );
    const a = await screen.findByRole("link", { name: /download/i });
    expect(a.getAttribute("download")).toBe("predictions.csv");
  });
});
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd frontend && pnpm vitest src/components/common/ArtifactTree.test.tsx --run`
Expected: FAIL — `download` attribute has no value (HTML treats valueless `download` as empty string in the DOM, so getAttribute returns "")

- [ ] **Step 3: Pass `download={name}` value**

```tsx
// frontend/src/components/common/ArtifactTree.tsx — find the file's <a> and update
<a
  className="inline-flex items-center text-xs text-primary hover:underline"
  href={`/api/v1/runs/${runId}/artifacts/download?path=${encodeURIComponent(e.path)}`}
  download={name}
>
  <Download className="mr-1 h-3 w-3" />
  download
</a>
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend && pnpm vitest src/components/common/ArtifactTree.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/common/ArtifactTree.tsx \
  frontend/src/components/common/ArtifactTree.test.tsx
git commit -m "fix(artifacts): pass basename via download attribute"
```

---

### Task 3.6: Add `download="predictions.csv"` to PredictSummary download button

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/components/jobs/PredictSummary.tsx`

- [ ] **Step 1: Update the button's `<a>`**

```tsx
// frontend/src/components/jobs/PredictSummary.tsx — find the predictions.csv anchor
<a
  href={`/api/v1/runs/${job.mlflow_run_id}/artifacts/download?path=predictions.csv`}
  download="predictions.csv"
>
  <Download className="mr-2 h-4 w-4" />
  Download predictions.csv
</a>
```

- [ ] **Step 2: Run frontend test suite to confirm no regression**

Run: `cd frontend && pnpm vitest --run`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jobs/PredictSummary.tsx
git commit -m "fix(jobs): predict download button declares basename for browser save dialog"
```

---

### Task 3.7: i18n key `common.openInMlflow`

**Files:**

- Modify: `LOLDAY_REPO/frontend/src/i18n/zh-TW.json`
- Modify: `LOLDAY_REPO/frontend/src/i18n/en.json`
- Modify: `LOLDAY_REPO/frontend/src/components/common/OpenInMlflowButton.tsx` (only if it currently hard-codes the string)

- [ ] **Step 1: Inspect existing button component for i18n use**

Run: `grep -n "Open in MLflow\|t(" frontend/src/components/common/OpenInMlflowButton.tsx`

If the literal string is present, plumb it through `useTranslation` similar to other components in `frontend/src/components/jobs/`. If already i18n'd, this task is just adding the missing keys.

- [ ] **Step 2: Add the nested keys**

```json
// frontend/src/i18n/zh-TW.json — add under "common"
{
  "common": {
    "openInMlflow": "在 MLflow 中開啟"
  }
}

// frontend/src/i18n/en.json — add under "common"
{
  "common": {
    "openInMlflow": "Open in MLflow"
  }
}
```

(Only add the leaf if `common` already exists; otherwise create it. Per project rule, never use flat dot-keys.)

- [ ] **Step 3: Update the component to read from i18n if it doesn't yet**

```tsx
// frontend/src/components/common/OpenInMlflowButton.tsx — if not yet i18n'd
import { useTranslation } from "react-i18next";

export function OpenInMlflowButton({
  experimentId,
  runId,
  size = "sm",
}: Props) {
  const { t } = useTranslation();
  let href = "/mlflow/";
  if (experimentId && runId) {
    href = `/mlflow/#/experiments/${experimentId}/runs/${runId}`;
  } else if (experimentId) {
    href = `/mlflow/#/experiments/${experimentId}`;
  }
  return (
    <Button asChild variant="outline" size={size}>
      <a href={href} target="_blank" rel="noopener noreferrer">
        <ExternalLink className="mr-2 h-4 w-4" />
        {t("common.openInMlflow")}
      </a>
    </Button>
  );
}
```

- [ ] **Step 4: Run vitest**

Run: `cd frontend && pnpm vitest --run`
Expected: ALL PASS (Job Detail Shell test from Task 3.3 still passes — react-i18next test setup typically returns the key when no namespace, so adjust the assertion to `getByRole("link", { name: /open in mlflow|openInMlflow/i })` if needed.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/i18n/zh-TW.json frontend/src/i18n/en.json \
  frontend/src/components/common/OpenInMlflowButton.tsx
git commit -m "i18n(common): add openInMlflow key (zh-TW + en)"
```

---

### Task 3.8: Playwright e2e — Run Detail redirect

**Files:**

- Create: `LOLDAY_REPO/frontend/tests/e2e/run-detail-redirect.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
// frontend/tests/e2e/run-detail-redirect.spec.ts
import { test, expect } from "@playwright/test";

test("visiting /runs/<exp>/<run> redirects to /jobs/<id> when run has lolday.job_id tag", async ({
  page,
  request,
}) => {
  // Pre-condition: a known job exists with mlflow_run_id and mlflow_experiment_id
  // (rely on seed data or set up via API call here)
  const job = await request
    .get("/api/v1/jobs?status=succeeded&limit=1")
    .then((r) => r.json())
    .then((rows) => rows[0]);
  if (!job?.mlflow_run_id || !job?.mlflow_experiment_id) {
    test.skip(true, "no succeeded job with mlflow ids available");
    return;
  }
  await page.goto(`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`);
  await page.waitForURL(`**/jobs/${job.id}`);
  expect(page.url()).toContain(`/jobs/${job.id}`);
});
```

- [ ] **Step 2: Run e2e**

Run: `cd frontend && pnpm playwright test tests/e2e/run-detail-redirect.spec.ts`
Expected: PASS (or SKIP if seed data absent)

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/e2e/run-detail-redirect.spec.ts
git commit -m "test(e2e): run detail deeplink redirects to job detail"
```

---

### Task 3.9: Playwright e2e — baseline train + eval flow

**Files:**

- Create: `LOLDAY_REPO/frontend/tests/e2e/baseline-train-eval-flow.spec.ts`

- [ ] **Step 1: Write the spec**

```ts
// frontend/tests/e2e/baseline-train-eval-flow.spec.ts
import { test, expect } from "@playwright/test";

test("train then evaluate, confusion matrix card visible with Malware row positive", async ({
  page,
}) => {
  // This e2e is opt-in and requires a working maldet 2.0 detector. Skip in CI
  // unless DETECTOR_ID env is set.
  const detectorId = process.env.DETECTOR_ID;
  test.skip(!detectorId, "set DETECTOR_ID env to run baseline e2e");

  // Submit train job via UI (simplified)
  await page.goto("/jobs/new");
  // ... wire form to submit train against detectorId
  // Wait for SUCCEEDED via polling
  await page.goto("/jobs"); // jobs list
  // Click latest train job → Job Detail
  // Submit evaluate using trained model
  // Wait SUCCEEDED → Job Detail
  // Assert CM card present with Malware as first label
  await expect(page.getByText(/per-class metrics/i)).toBeVisible();
  await expect(page.getByText(/malware.*\(positive\)/i)).toBeVisible();
  await expect(page.getByText(/confusion matrix/i)).toBeVisible();
  // Assert top-left of CM has "Pred Benign" + "True Benign" intersection,
  // bottom-right has "Pred Malware" + "True Malware" — visually placed
  // such that Malware (positive) is the second row/col.
});
```

(This e2e is heavyweight and tied to detector availability; mark `.skip` in CI by default.)

- [ ] **Step 2: Commit (no run unless DETECTOR_ID seeded)**

```bash
git add frontend/tests/e2e/baseline-train-eval-flow.spec.ts
git commit -m "test(e2e): opt-in baseline train+eval flow with CM assertion"
```

---

# Phase 4 — Operations: cutover

> Phase 4 tasks run on `SERVER30`. Operator must have `kubectl`, `mc`, and `jq` available. No sudo required.

### Task 4.1: Write `scripts/wipe-mlflow-history.sh`

**Files:**

- Create: `LOLDAY_REPO/scripts/wipe-mlflow-history.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Soft-delete all MLflow runs/experiments/registered models, then run gc.
# Sudo-free; uses cluster-internal MLflow service via kubectl exec.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
MLFLOW_POD="${MLFLOW_POD:-$(kubectl -n "$NAMESPACE" get pod -l app=mlflow-server -o jsonpath='{.items[0].metadata.name}')}"
MLFLOW_API="${MLFLOW_API:-http://mlflow-server.${NAMESPACE}.svc/api/2.0/mlflow}"

echo "Counting current resources via $MLFLOW_API ..."
NUM_EXP=$(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" | jq '.experiments | length')
NUM_RUNS=0
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" | jq -r '.experiments[].experiment_id'); do
  RUN_COUNT=$(curl -fsS "$MLFLOW_API/runs/search" -X POST -H 'Content-Type: application/json' \
              -d "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000}" | jq '.runs | length')
  NUM_RUNS=$((NUM_RUNS + RUN_COUNT))
done
NUM_MODELS=$(curl -fsS "$MLFLOW_API/registered-models/search?max_results=1000" | jq '.registered_models | length')

cat <<EOF

This will permanently delete:
  - $NUM_EXP experiments (excluding Default id=0)
  - $NUM_RUNS runs across all experiments
  - $NUM_MODELS registered models with all versions
  + run mlflow gc to permanently purge soft-deleted runs and free artifact storage.

EOF

read -r -p "Continue? (yes/NO): " ans
if [[ "$ans" != "yes" ]]; then
  echo "Aborted."; exit 1
fi

echo "[1/4] Soft-deleting all runs..."
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" | jq -r '.experiments[].experiment_id'); do
  for run in $(curl -fsS "$MLFLOW_API/runs/search" -X POST -H 'Content-Type: application/json' \
                  -d "{\"experiment_ids\":[\"$exp\"],\"max_results\":1000}" | jq -r '.runs[].info.run_id'); do
    curl -fsS "$MLFLOW_API/runs/delete" -X POST -H 'Content-Type: application/json' \
         -d "{\"run_id\":\"$run\"}" > /dev/null
  done
done

echo "[2/4] Deleting registered model versions and shells..."
for name in $(curl -fsS "$MLFLOW_API/registered-models/search?max_results=1000" | jq -r '.registered_models[].name'); do
  for v in $(curl -fsS --get "$MLFLOW_API/model-versions/search" \
                 --data-urlencode "filter=name='$name'" --data-urlencode "max_results=1000" \
                 | jq -r '.model_versions[].version'); do
    curl -fsS "$MLFLOW_API/model-versions/delete" -X POST -H 'Content-Type: application/json' \
         -d "{\"name\":\"$name\",\"version\":\"$v\"}" > /dev/null
  done
  curl -fsS "$MLFLOW_API/registered-models/delete" -X POST -H 'Content-Type: application/json' \
       -d "{\"name\":\"$name\"}" > /dev/null
done

echo "[3/4] Soft-deleting experiments (skipping Default id=0)..."
for exp in $(curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" | jq -r '.experiments[].experiment_id'); do
  [[ "$exp" == "0" ]] && continue
  curl -fsS "$MLFLOW_API/experiments/delete" -X POST -H 'Content-Type: application/json' \
       -d "{\"experiment_id\":\"$exp\"}" > /dev/null
done

echo "[4/4] Running mlflow gc inside pod $MLFLOW_POD ..."
kubectl -n "$NAMESPACE" exec "$MLFLOW_POD" -- \
  mlflow gc --backend-store-uri "$(kubectl -n "$NAMESPACE" exec "$MLFLOW_POD" -- printenv MLFLOW_BACKEND_STORE_URI)"

echo "Wipe complete."
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/wipe-mlflow-history.sh
```

- [ ] **Step 3: Dry-run review (do NOT execute on production yet)**

Run: `bash -n scripts/wipe-mlflow-history.sh`
Expected: no syntax errors

- [ ] **Step 4: Commit**

```bash
git add scripts/wipe-mlflow-history.sh
git commit -m "feat(ops): wipe-mlflow-history.sh for cutover (sudo-free)"
```

---

### Task 4.2: Open lolday PR and merge

**Files:**

- (Git operation only — no file edits)

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(maldet+jobs): bump maldet to 2.0 with explicit positive_class + remove redundant Run Detail page" \
  --body "$(cat <<'EOF'
Spec: docs/superpowers/specs/2026-05-01-maldet-2-and-runs-cleanup-design.md
Plan: docs/superpowers/plans/2026-05-02-maldet-2-and-runs-cleanup.md

## Summary
- Bump maldet to 2.x (depends on PyPI release)
- backend: RFC 6266 Content-Disposition on artifact download; BACKEND_MAINTENANCE_MODE flag
- frontend: replace Run Detail page with redirect; Job Detail header gets Open in MLflow; ArtifactTree download attr
- ops: scripts/wipe-mlflow-history.sh

## Test plan
- [ ] backend: pytest passes
- [ ] frontend: vitest passes
- [ ] frontend: playwright opt-in tests pass when seed data present
- [ ] manual: download a CSV from ArtifactTree → browser saves with correct filename
- [ ] manual: visit /runs/<exp>/<run> → redirects to /jobs/<id>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: Wait for CI green and merge**

```bash
gh pr checks --watch
gh pr merge --squash
git checkout main && git pull
```

---

### Task 4.3: Set `BACKEND_MAINTENANCE_MODE=1` and announce maintenance

**Files:**

- Modify: `LOLDAY_REPO/charts/lolday/values.yaml` (env var injection) OR `kubectl set env`

- [ ] **Step 1: Patch the backend Deployment env**

```bash
kubectl -n lolday set env deployment/backend BACKEND_MAINTENANCE_MODE=1
kubectl -n lolday rollout status deployment/backend
```

- [ ] **Step 2: Verify endpoint returns 503**

```bash
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer <test-token>" \
  https://lolday.example/api/v1/jobs -d '{}'
# Expected: HTTP/1.1 503 Service Unavailable
#           Retry-After: 3600
```

- [ ] **Step 3: Send Discord announcement**

Use the configured operator Discord channel (Captain Hook per memory note). Send: "🛠 Lolday platform under maintenance for ~4-6h. New job submissions paused. ETA: <time>."

---

### Task 4.4: Backup Postgres + MinIO

**Files:**

- (No source changes; operator runs commands)

- [ ] **Step 1: pg_dumpall**

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
PG_POD=$(kubectl -n lolday get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday exec "$PG_POD" -- pg_dumpall -U postgres > "$HOME/backup-pgdump-$TS.sql"
```

- [ ] **Step 2: MinIO mirror**

```bash
mc mirror lolday-minio/mlflow-artifacts "$HOME/backup-mlflow-artifacts-$TS/"
```

- [ ] **Step 3: Verify backups**

```bash
ls -lh "$HOME/backup-pgdump-$TS.sql"
du -sh "$HOME/backup-mlflow-artifacts-$TS/"
mc ls lolday-minio/mlflow-artifacts | wc -l           # source count
find "$HOME/backup-mlflow-artifacts-$TS/" -type f | wc -l  # backup count
# expect counts to match (or backup count >= source if more files exist on disk)
```

- [ ] **Step 4: Note backup location for rollback**

Record: `BACKUP_TIMESTAMP=$TS` and the filenames in operator's notes / Discord channel for the maintenance window.

---

### Task 4.5: Wipe Lolday DB

**Files:**

- (Operator SQL session)

- [ ] **Step 1: Connect to lolday DB**

```bash
PG_POD=$(kubectl -n lolday get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday exec -it "$PG_POD" -- psql -U postgres -d lolday
```

- [ ] **Step 2: Run TRUNCATE inside a transaction**

```sql
BEGIN;

TRUNCATE
    model_transition_log,
    model_version,
    job_event,
    job
RESTART IDENTITY CASCADE;

UPDATE detector_version SET mlflow_experiment_id = NULL;

-- Sanity: verify all four tables are empty and no detector_version still references mlflow_experiment_id
SELECT
  (SELECT count(*) FROM job)                  AS job_rows,
  (SELECT count(*) FROM job_event)            AS event_rows,
  (SELECT count(*) FROM model_version)        AS mv_rows,
  (SELECT count(*) FROM model_transition_log) AS mt_rows,
  (SELECT count(*) FROM detector_version WHERE mlflow_experiment_id IS NOT NULL) AS stale_dv;

-- All five counts must be 0. If yes, COMMIT. If not, ROLLBACK and investigate.
COMMIT;
```

- [ ] **Step 3: Verify**

```sql
\q
```

---

### Task 4.6: Wipe MLflow

**Files:**

- (Operator runs `scripts/wipe-mlflow-history.sh`)

- [ ] **Step 1: Pull latest scripts**

```bash
cd ~/repos/lolday && git pull
```

- [ ] **Step 2: Execute wipe**

```bash
bash scripts/wipe-mlflow-history.sh
# Read counts, type "yes" to confirm.
```

- [ ] **Step 3: Verify**

```bash
curl -fsS "$MLFLOW_API/experiments/search?max_results=1000" | jq '.experiments[].name'
# Expected: only the Default experiment shell remains (or empty array if MLflow is configured to not have one)
mc ls lolday-minio/mlflow-artifacts | wc -l
# Expected: significantly reduced from before
```

---

### Task 4.7: Rebuild build-helper image

**Files:**

- Modify: `LOLDAY_REPO/charts/lolday/helpers.lock` (regenerated)

- [ ] **Step 1: Run build-helpers script**

```bash
cd ~/repos/lolday
git checkout -b chore/rebuild-build-helper-maldet-2
bash scripts/build-helpers.sh
```

- [ ] **Step 2: Verify lockfile changed**

```bash
git diff --stat charts/lolday/helpers.lock
# Expected: build-helper digest changed
```

- [ ] **Step 3: Commit, push, merge**

```bash
git add charts/lolday/helpers.lock
git commit -m "chore(helpers): rebuild build-helper for maldet 2.0"
git push -u origin chore/rebuild-build-helper-maldet-2
gh pr create --title "chore(helpers): rebuild build-helper for maldet 2.0" \
  --body "Plan: docs/superpowers/plans/2026-05-02-maldet-2-and-runs-cleanup.md (Task 4.7)"
gh pr merge --squash
git checkout main && git pull
```

---

### Task 4.8: Update each detector repo

**Files:**

- Per detector repo: `maldet.toml`, `pyproject.toml`

- [ ] **Step 1: For each detector repo, apply changes**

Operator runs the following loop (replace `DETECTORS` with actual list):

```bash
for repo in islab-malware-detector other-detector-1 other-detector-2; do
  cd ~/repos/$repo
  git checkout main && git pull
  git checkout -b chore/maldet-2-upgrade

  # Edit maldet.toml — add/change [output].positive_class, [compat] block
  # Use your editor; example diff:
  #
  #   [output]
  #   task = "binary_classification"
  # - classes = ["Malware", "Benign"]
  # + classes = ["Benign", "Malware"]
  # + positive_class = "Malware"
  #   score_range = [0.0, 1.0]
  #
  #   [compat]
  # - schema_version = 1
  # - min_maldet = "1.0"
  # + schema_version = 2
  # + min_maldet = "2.0"

  # Edit pyproject.toml — bump maldet line
  # - "maldet>=1.1,<2"
  # + "maldet>=2.0,<3"

  # Lock + commit + tag
  uv lock || true     # if your detector uses uv
  git add maldet.toml pyproject.toml uv.lock 2>/dev/null
  git commit -m "chore(maldet): upgrade to schema_version=2 with explicit positive_class"
  git tag v2.0.0
  git push -u origin chore/maldet-2-upgrade --follow-tags
  # Open + merge PR via gh
  gh pr create --title "chore(maldet): upgrade to v2.0" --body "Phase 4.8 of lolday cutover"
  gh pr merge --squash
  cd -
done
```

- [ ] **Step 2: Trigger detector image build via Lolday backend for each detector**

```bash
# Acquire user token (your auth flow)
TOKEN=...
for detector_id in $(curl -fsS -H "Authorization: Bearer $TOKEN" \
                       https://lolday.example/api/v1/detectors | jq -r '.[].id'); do
  curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    "https://lolday.example/api/v1/detectors/$detector_id/build" \
    -d '{"git_tag":"v2.0.0"}'
done
```

- [ ] **Step 3: Wait for builds to succeed**

Poll Lolday's `/api/v1/detectors/<id>/builds` or watch the UI. Each build SUCCEEDED before proceeding.

- [ ] **Step 4: Manual smoke**

```bash
docker run --rm --entrypoint maldet \
  harbor.lolday.svc:80/lolday/<detector-name>:v2.0.0 describe
# Expected: prints manifest with positive_class field present.
```

---

### Task 4.9: Submit baseline train + evaluate + predict per detector

**Files:**

- (Operator runs via Lolday UI or CLI)

- [ ] **Step 1: For each detector, submit a train job**

Use Lolday UI `/jobs/new` (will fail because maintenance flag is on — switch the flag temporarily):

```bash
kubectl -n lolday set env deployment/backend BACKEND_MAINTENANCE_MODE=0
kubectl -n lolday rollout status deployment/backend
```

(Maintenance mode is now off; baseline submissions are allowed. Remember to re-enable if more wipe-related work is needed.)

Submit a train job per detector via UI or API. Wait for SUCCEEDED.

- [ ] **Step 2: Submit evaluate using the trained model**

For each detector, use the `Source model` selector on /jobs/new evaluate form to pick the just-trained model. Submit, wait SUCCEEDED.

- [ ] **Step 3: Submit predict using the trained model**

Same as Step 2 but evaluate→predict. Wait SUCCEEDED.

- [ ] **Step 4: SQL sanity per detector**

```sql
SELECT id, type, status,
       summary_metrics->'confusion_matrix' IS NOT NULL AS has_cm,
       summary_metrics->'per_class' IS NOT NULL AS has_pc,
       summary_metrics->'prediction_summary' IS NOT NULL AS has_pred
FROM job
WHERE detector_version_id IN (
  SELECT id FROM detector_version
  WHERE detector_id = '<detector-id>'
)
ORDER BY finished_at DESC LIMIT 6;
-- Train rows: has_cm=false (only train metrics); ok
-- Evaluate rows: has_cm=true AND has_pc=true (both must be true)
-- Predict rows: has_pred=true
```

---

### Task 4.10: Acceptance verification — UI walk-through

**Files:**

- (Operator interactive verification)

- [ ] **Step 1: Issue 1 (label)**

For one finished evaluate job:

- Open `/jobs/<eval-job-id>` → Summary tab
- Verify "Per-class metrics" card: `Malware` row tagged `(positive)` and listed first
- Verify "Confusion matrix" card: top-left cell intersection = `True Benign` × `Pred Benign`; bottom-right = `True Malware` × `Pred Malware`. The Malware-Malware diagonal should hold the highest count for a working model.

For one finished predict job:

- Verify "Predicted class distribution" bar — Malware-tagged input samples are predicted as Malware.

- [ ] **Step 2: Issue 2 (logs)**

For each newly created baseline job:

- Open Logs tab → see `stage_begin`, `data_loaded`, `metric`, `stage_end` events
- Tail is not `(no output)`

- [ ] **Step 3: Issue 3 (download)**

For one finished evaluate job:

- Artifacts tab → click `download` next to `metrics.json` → browser save dialog defaults to `metrics.json` (NOT `download`)
- For one finished predict job: PredictSummary card → "Download predictions.csv" button → save dialog defaults to `predictions.csv`

- [ ] **Step 4: Issue 4 (run page)**

- Job Detail header (any job): "Open in MLflow" button visible; click → opens MLflow run page in new tab
- No "Open run ↗" tab in Job Detail
- Visit a Run Detail deeplink: `/runs/<expId>/<runId>` from a baseline job → URL changes to `/jobs/<jobId>` automatically
- Visit a fabricated orphan deeplink (e.g. an MLflow-only run created via API): redirects to `/mlflow/#/experiments/.../runs/...` in a new tab

- [ ] **Step 5: Verify lazy-create worked**

```sql
-- Confirm new mlflow_experiment_id values were lazy-created post-wipe:
SELECT id, mlflow_experiment_id
FROM detector_version
WHERE mlflow_experiment_id IS NOT NULL
ORDER BY id;
-- Expected: each detector_version that ran a baseline now has a fresh experiment_id
```

---

### Task 4.11: Close the maintenance window

**Files:**

- (No source changes)

- [ ] **Step 1: Confirm maintenance flag is off**

```bash
kubectl -n lolday get deployment backend -o yaml | grep -A 1 BACKEND_MAINTENANCE_MODE
# Expected: value: "0" (or absent)
```

If still `1`:

```bash
kubectl -n lolday set env deployment/backend BACKEND_MAINTENANCE_MODE=0
kubectl -n lolday rollout status deployment/backend
```

- [ ] **Step 2: Smoke test job submission via UI**

Submit a small detector test job (e.g., a tiny train job from the lab user account). Verify it queues and starts.

- [ ] **Step 3: Discord announcement**

Send: "✅ Lolday maintenance complete. New baselines verified. Submissions are open. See [acceptance summary or PR link]."

- [ ] **Step 4: Archive backups (after 7 days)**

Operator note: in 7 days, delete `$HOME/backup-pgdump-*.sql` and `$HOME/backup-mlflow-artifacts-*/` if no rollback was triggered.

---

# Self-Review Checklist

After execution, double-check:

- [ ] All four user-reported issues addressed (per spec §7.7 acceptance table)
- [ ] All breaking changes from spec §3.3 enacted (positive_class required, schema_version=2, trainer signature, no model carry-over, run page redirect)
- [ ] All test files in spec §8 created or updated
- [ ] No remaining hard-coded `"Malware"` in maldet (`grep -rn '"Malware"' MALDET_REPO/maldet/` should return only enum/test usage)
- [ ] Lolday `helpers.lock` reflects rebuilt build-helper digest
- [ ] All detector repos tagged `v2.0.0` and corresponding images pushed
- [ ] `summary_metrics.confusion_matrix` and `.per_class` both populated for the latest evaluate jobs
- [ ] No 503 from `POST /jobs` after maintenance window closes

---

End of plan.
