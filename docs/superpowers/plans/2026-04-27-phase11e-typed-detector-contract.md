# Phase 11e Typed Detector Contract — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the maldet manifest the typed contract between detectors and the platform — JSON Schema for hyperparameters auto-derived from each stage's Pydantic config class, embedded in the manifest at build time, validated at submit time, rendered as RJSF on the frontend; consolidate run-time metrics into `job_events` as canonical with `job.summary_metrics` as a reconciler-projected read model.

**Architecture:** maldet 1.1.0 introduces `Stage.config_class` (import path) + `Stage.params_schema` (auto-derived JSON Schema) + `confusion_matrix` event kind + lint enforcing `extra="forbid"`. elfrfdet 3.0.0 / elfcnndet 3.0.0 add Pydantic config classes per stage. Lolday backend phase11e exposes `manifest` on `VersionDetailRead`, replaces hand-rolled `validate_user_params` with `jsonschema`, projects events into `summary_metrics` on `stage_end`. Lolday frontend phase11e renders RJSF, drops the JSON textarea, restores the manifest viewer, adds a Final metrics tile column to the job list.

**Tech Stack:** Python 3.12 (maldet, lolday backend), TypeScript / React (frontend), Pydantic v2, jsonschema (Draft 2020-12), `@rjsf/core`, FastAPI, SQLAlchemy/PostgreSQL, Alembic, Volcano, Helm.

**Repos involved:**
- `/home/bolin8017/Documents/repositories/maldet` (PyPI release, github `bolin8017/maldet`)
- `/home/bolin8017/Documents/repositories/elfrfdet` (github `bolin8017/elfrfdet`)
- `/home/bolin8017/Documents/repositories/elfcnndet` (github `bolin8017/elfcnndet`)
- `/home/bolin8017/Documents/repositories/lolday` (github `bolin8017/lolday`)

**Spec:** `/home/bolin8017/Documents/repositories/lolday/docs/superpowers/specs/2026-04-27-phase11e-typed-detector-contract-design.md`

---

## File Structure (which file does what)

### maldet 1.1.0
- `src/maldet/events/kinds.py` — add `CONFUSION_MATRIX` event kind + required fields
- `src/maldet/evaluators/binary.py` — emit `confusion_matrix` event
- `src/maldet/manifest.py` — `StageSpec` adds `config_class: str` (required) + `params_schema: dict[str, Any]` (required)
- `src/maldet/commands/introspect_schema.py` — new subcommand
- `src/maldet/commands/check.py` — add `extra="forbid"` lint
- `src/maldet/cli.py` — register introspect-schema
- `src/maldet/templates/sklearn_basic/{configs.py,maldet.toml.j2}` — scaffold updated
- `src/maldet/templates/lightning_cnn/{configs.py,maldet.toml.j2}` — scaffold updated
- `tests/events/test_kinds_confusion_matrix.py` — new
- `tests/test_introspect_schema.py` — new
- `tests/test_check_strict_lint.py` — new
- `tests/evaluators/test_binary_emits_confusion_matrix.py` — new
- `pyproject.toml` — version bump 1.0.8 → 1.1.0
- `CHANGELOG.md` — new entry

### elfrfdet 3.0.0
- `src/elfrfdet/configs.py` — new: `TrainConfig`, `EvaluateConfig`, `PredictConfig` (Pydantic BaseModel)
- `maldet.toml` — `[stages.train].config_class`, `[stages.evaluate].config_class`, `[stages.predict].config_class`; bump version
- `pyproject.toml` — bump maldet pin to `>=1.1,<2.0`
- `tests/test_manifest.py` — new
- `tests/test_configs.py` — new
- `CHANGELOG.md` — 3.0.0 BREAKING entry

### elfcnndet 3.0.0
- (same shape as elfrfdet, with Lightning-specific fields)

### Lolday backend phase11e
- `backend/app/schemas/detector.py` — `VersionDetailRead.manifest`
- `backend/app/schemas/job.py` — `JobSummary.summary_metrics`
- `backend/app/services/jobs_params_validate.py` — new (jsonschema-based)
- `backend/app/services/jobs_params_guard.py` — DELETE
- `backend/tests/test_services_jobs_params_guard.py` — DELETE
- `backend/app/routers/jobs.py` — call new validator
- `backend/app/reconciler.py` — `_project_summary_metrics` on stage_end
- `backend/tests/test_jsonschema_validate_params.py` — new
- `backend/tests/test_reconciler_summary_projection.py` — new
- `backend/tests/test_jobs_create_v11e.py` — new
- `backend/tests/test_schemas_version_detail_read.py` — new
- `lolday/scripts/backfill-summary-metrics.py` — new (optional one-shot)
- `lolday/scripts/deploy.sh` — bump default tags

### Lolday frontend phase11e
- `frontend/src/api/schema.gen.ts` — regenerate
- `frontend/src/components/forms/JobSubmitForm.tsx` — RJSF replaces textarea
- `frontend/src/components/forms/JobSubmitForm.logic.ts` — drop `parseParams`
- `frontend/src/routes/_authed.detectors.$id.tsx` — restore manifest viewer
- `frontend/src/routes/_authed.jobs.$id.tsx` — chart visibility on `hasTimeSeries`
- `frontend/src/routes/_authed.jobs.tsx` (or jobs list route) — Final metrics tile column
- `frontend/tests/unit/components/JobSubmitForm.test.tsx` — replace tests
- `frontend/tests/unit/components/JobsList.test.tsx` — new
- `frontend/tests/e2e/phase11e-full-flow.spec.ts` — new opt-in spec

---

# Batch 1 — maldet 1.1.0

> Working dir: `/home/bolin8017/Documents/repositories/maldet`
> Use `uv run pytest …` for test runs.

## Task 1.1: Add `confusion_matrix` event kind

**Files:**
- Modify: `src/maldet/events/kinds.py`
- Test: `tests/events/test_kinds_confusion_matrix.py`

- [ ] **Step 1: Write the failing test**

`tests/events/test_kinds_confusion_matrix.py`:
```python
"""confusion_matrix event kind — emitted by evaluators after MetricReport."""

import pytest

from maldet.events.kinds import EventKind, validate_payload


def test_confusion_matrix_kind_in_enum():
    assert EventKind.CONFUSION_MATRIX.value == "confusion_matrix"


def test_confusion_matrix_validates_with_labels_and_matrix():
    validate_payload(EventKind.CONFUSION_MATRIX, {"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]})


def test_confusion_matrix_rejects_missing_labels():
    with pytest.raises(ValueError, match="labels"):
        validate_payload(EventKind.CONFUSION_MATRIX, {"matrix": [[1, 0], [0, 1]]})


def test_confusion_matrix_rejects_missing_matrix():
    with pytest.raises(ValueError, match="matrix"):
        validate_payload(EventKind.CONFUSION_MATRIX, {"labels": ["a", "b"]})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/bolin8017/Documents/repositories/maldet
uv run pytest tests/events/test_kinds_confusion_matrix.py -v
```
Expected: FAIL with `AttributeError: CONFUSION_MATRIX` or `KeyError`.

- [ ] **Step 3: Add CONFUSION_MATRIX to kinds**

Edit `src/maldet/events/kinds.py`:
```python
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
    CONFUSION_MATRIX = "confusion_matrix"


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
    EventKind.CONFUSION_MATRIX: ("labels", "matrix"),
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/events/test_kinds_confusion_matrix.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/maldet/events/kinds.py tests/events/test_kinds_confusion_matrix.py
git commit -m "feat(events): add confusion_matrix event kind"
```

---

## Task 1.2: BinaryClassification emits confusion_matrix

**Files:**
- Modify: `src/maldet/evaluators/binary.py`
- Test: `tests/evaluators/test_binary_emits_confusion_matrix.py`

- [ ] **Step 1: Write the failing test**

`tests/evaluators/test_binary_emits_confusion_matrix.py`:
```python
"""Evaluator emits confusion_matrix event after metrics are computed."""

import json
from pathlib import Path

import numpy as np
import pytest

from maldet.evaluators.binary import BinaryClassification
from maldet.events.jsonl import JsonlEventLogger
from maldet.types import Sample


class _StubReader:
    def __init__(self, samples):
        self._samples = samples
    def __iter__(self):
        return iter(self._samples)


class _StubExtractor:
    def extract(self, sample):
        return np.array([1.0, 0.0]) if sample.label == "Malware" else np.array([0.0, 1.0])


class _PerfectModel:
    def predict(self, X):  # noqa: N803
        return np.array([1 if x[0] > 0.5 else 0 for x in X])


def _samples():
    return [
        Sample(sha256="a", path=Path("/x"), label="Malware", metadata={}),
        Sample(sha256="b", path=Path("/y"), label="Benign", metadata={}),
        Sample(sha256="c", path=Path("/z"), label="Malware", metadata={}),
    ]


def test_evaluate_emits_confusion_matrix(tmp_path):
    log_path = tmp_path / "events.jsonl"
    logger = JsonlEventLogger(log_path)
    evaluator = BinaryClassification()

    evaluator.evaluate(
        model=_PerfectModel(),
        reader=_StubReader(_samples()),
        extractor=_StubExtractor(),
        logger=logger,
    )

    lines = log_path.read_text().strip().splitlines()
    cm_records = [json.loads(line) for line in lines if json.loads(line).get("kind") == "confusion_matrix"]
    assert len(cm_records) == 1
    cm = cm_records[0]
    assert cm["labels"] == ["Benign", "Malware"]
    assert cm["matrix"] == [[1, 0], [0, 2]]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/evaluators/test_binary_emits_confusion_matrix.py -v
```
Expected: FAIL — no `confusion_matrix` record in events.

- [ ] **Step 3: Read existing evaluator and add the emit call**

First read the current implementation:
```bash
cat src/maldet/evaluators/binary.py
```

Add the following to `BinaryClassification.evaluate`, immediately after `MetricReport` is constructed and the per-metric `log_metric` calls:
```python
from sklearn.metrics import confusion_matrix as sk_confusion_matrix

# At the end of evaluate(), after metric events are logged:
labels_sorted = ["Benign", "Malware"]   # canonical label order
cm = sk_confusion_matrix(
    y_true=[1 if s.label == "Malware" else 0 for s in samples],
    y_pred=preds,
    labels=[0, 1],
)
logger.log_event(
    "confusion_matrix",
    labels=labels_sorted,
    matrix=cm.tolist(),
)
```

(Adjust variable names to match the existing function — the engineer should keep the existing structure and only ADD the four lines above plus the import.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/evaluators/test_binary_emits_confusion_matrix.py -v
uv run pytest tests/evaluators/ -v
```
Expected: new test passes; existing evaluator tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/maldet/evaluators/binary.py tests/evaluators/test_binary_emits_confusion_matrix.py
git commit -m "feat(evaluators): emit confusion_matrix event from BinaryClassification"
```

---

## Task 1.3: StageSpec adds `config_class` and `params_schema`

**Files:**
- Modify: `src/maldet/manifest.py`
- Modify: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_manifest.py`:
```python
def test_stage_requires_config_class():
    """Phase 11e: each stage in maldet.toml must declare config_class import path."""
    import pytest
    from maldet.manifest import DetectorManifest

    bad = {
        "detector": {"name": "x", "version": "0.1", "framework": "sklearn"},
        "input": {"binary_format": "elf"},
        "output": {"task": "binary_classification"},
        "resources": {},
        "lifecycle": {},
        "artifacts": {"model": {"path": "model/", "type": "dir"}},
        "stages": {"train": {"reader": "m:R"}},  # missing config_class + params_schema
    }
    with pytest.raises(ValueError):
        DetectorManifest.model_validate(bad)


def test_stage_accepts_config_class_and_params_schema():
    from maldet.manifest import DetectorManifest

    good = {
        "detector": {"name": "x", "version": "0.1", "framework": "sklearn"},
        "input": {"binary_format": "elf"},
        "output": {"task": "binary_classification"},
        "resources": {},
        "lifecycle": {},
        "artifacts": {"model": {"path": "model/", "type": "dir"}},
        "stages": {
            "train": {
                "reader": "m:R",
                "config_class": "elfrfdet.configs:TrainConfig",
                "params_schema": {"type": "object", "properties": {}},
            }
        },
    }
    m = DetectorManifest.model_validate(good)
    assert m.stages["train"].config_class == "elfrfdet.configs:TrainConfig"
    assert m.stages["train"].params_schema == {"type": "object", "properties": {}}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_manifest.py::test_stage_requires_config_class tests/test_manifest.py::test_stage_accepts_config_class_and_params_schema -v
```
Expected: FAIL — `StageSpec` has neither field.

- [ ] **Step 3: Add the fields to `StageSpec`**

Edit `src/maldet/manifest.py`:
```python
class StageSpec(_Frozen):
    reader: str | None = None
    extractor: str | None = None
    model: str | None = None
    trainer: str | None = None
    evaluator: str | None = None
    predictor: str | None = None
    # phase 11e — typed contract
    config_class: str           # import path "module.sub:ClassName" → Pydantic BaseModel
    params_schema: dict[str, Any]  # JSON Schema (auto-derived by `maldet introspect-schema`)
```

Required fields (no defaults) — Pydantic raises on missing.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_manifest.py -v
```
Expected: both new tests pass; existing tests may FAIL because their fixtures lack `config_class`/`params_schema`. Update those fixtures to include the new fields with placeholder values:
```python
"config_class": "x.configs:TrainConfig",
"params_schema": {"type": "object"},
```

Re-run until all pass.

- [ ] **Step 5: Commit**

```bash
git add src/maldet/manifest.py tests/test_manifest.py
git commit -m "feat(manifest): require config_class + params_schema on Stage"
```

---

## Task 1.4: `maldet introspect-schema` subcommand

**Files:**
- Create: `src/maldet/commands/introspect_schema.py`
- Modify: `src/maldet/cli.py`
- Test: `tests/test_introspect_schema.py`

- [ ] **Step 1: Write the failing test**

`tests/test_introspect_schema.py`:
```python
"""maldet introspect-schema — derive JSON Schema from a stage's config_class."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from maldet.cli import app


@pytest.fixture
def sample_pkg(tmp_path, monkeypatch):
    """Drop a minimal Pydantic config class on sys.path."""
    pkg = tmp_path / "samplepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "configs.py").write_text(
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "class TrainConfig(BaseModel):\n"
        "    model_config = ConfigDict(extra='forbid')\n"
        "    n_estimators: int = Field(default=100, ge=1)\n"
        "    max_depth: int | None = None\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return pkg


def test_introspect_emits_json_schema(sample_pkg, tmp_path):
    runner = CliRunner()
    out = tmp_path / "schema.json"
    result = runner.invoke(
        app,
        ["introspect-schema", "--config-class", "samplepkg.configs:TrainConfig", "--out", str(out)],
    )
    assert result.exit_code == 0, result.stdout
    import json
    schema = json.loads(out.read_text())
    assert schema["additionalProperties"] is False
    assert "n_estimators" in schema["properties"]
    assert schema["properties"]["n_estimators"]["minimum"] == 1


def test_introspect_rejects_non_basemodel(tmp_path, monkeypatch):
    pkg = tmp_path / "badpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "x.py").write_text("class NotAModel:\n    pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(app, ["introspect-schema", "--config-class", "badpkg.x:NotAModel"])
    assert result.exit_code != 0
    assert "BaseModel" in result.stdout


def test_introspect_rejects_extra_allow(tmp_path, monkeypatch):
    pkg = tmp_path / "loose"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "c.py").write_text(
        "from pydantic import BaseModel, ConfigDict\n"
        "class LooseConfig(BaseModel):\n"
        "    model_config = ConfigDict(extra='allow')\n"
        "    x: int = 1\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["introspect-schema", "--config-class", "loose.c:LooseConfig"])
    assert result.exit_code != 0
    assert "extra" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_introspect_schema.py -v
```
Expected: FAIL — command does not exist.

- [ ] **Step 3: Create the command module**

`src/maldet/commands/introspect_schema.py`:
```python
"""``maldet introspect-schema`` — auto-derive a stage's JSON Schema from its Pydantic config class.

Used by ``maldet build`` to populate ``manifest.stages.{stage}.params_schema``
without forcing detector authors to hand-write JSON Schema.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

app = typer.Typer(help="Auto-derive JSON Schema from a stage's Pydantic config class.")


def _load_class(dotted: str) -> type:
    if ":" not in dotted:
        raise typer.BadParameter(f"expected 'module.sub:Class', got {dotted!r}")
    mod_name, attr = dotted.split(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def introspect_schema(
    config_class: str = typer.Option(
        ..., "--config-class", help="Import path 'module.sub:ClassName' to a Pydantic BaseModel."
    ),
    out: Path | None = typer.Option(None, "--out", help="Write schema JSON to this file (else stdout)."),
) -> None:
    cls = _load_class(config_class)
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        typer.echo(f"error: {config_class} is not a pydantic.BaseModel subclass", err=True)
        raise typer.Exit(2)
    if cls.model_config.get("extra") != "forbid":
        typer.echo(
            f"error: {config_class} must set model_config = ConfigDict(extra='forbid')",
            err=True,
        )
        raise typer.Exit(2)
    schema: dict[str, Any] = cls.model_json_schema(mode="serialization")
    text = json.dumps(schema, indent=2, sort_keys=True)
    if out is not None:
        out.write_text(text + "\n")
    else:
        sys.stdout.write(text + "\n")
```

- [ ] **Step 4: Wire up the CLI**

Edit `src/maldet/cli.py`:
```python
from maldet.commands import introspect_schema as _introspect

# ... existing add_typer / command registrations ...
app.command("introspect-schema")(_introspect.introspect_schema)
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_introspect_schema.py -v
uv run pytest tests/test_cli.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/maldet/commands/introspect_schema.py src/maldet/cli.py tests/test_introspect_schema.py
git commit -m "feat(cli): add maldet introspect-schema for auto-derived params_schema"
```

---

## Task 1.5: `maldet check` enforces `extra="forbid"` lint

**Files:**
- Modify: `src/maldet/commands/check.py`
- Test: `tests/test_check_strict_lint.py`

- [ ] **Step 1: Write the failing test**

`tests/test_check_strict_lint.py`:
```python
"""maldet check fails when stage config_class doesn't set extra='forbid'."""

import textwrap

import pytest
from typer.testing import CliRunner

from maldet.cli import app


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Lay out a fake detector repo with one strict + one loose config class."""
    pkg = tmp_path / "loose_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "configs.py").write_text(textwrap.dedent("""\
        from pydantic import BaseModel, ConfigDict
        class LooseConfig(BaseModel):
            model_config = ConfigDict(extra='allow')
            n: int = 1
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    manifest = tmp_path / "maldet.toml"
    manifest.write_text(textwrap.dedent("""\
        [detector]
        name = "x"
        version = "0.1"
        framework = "sklearn"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"

        [resources]

        [lifecycle]

        [artifacts]
        model = { path = "model/", type = "dir" }

        [stages.train]
        config_class = "loose_pkg.configs:LooseConfig"
        params_schema = {"type" = "object"}
    """))
    return tmp_path


def test_check_rejects_loose_config_class(repo):
    runner = CliRunner()
    result = runner.invoke(app, ["check", "--manifest", str(repo / "maldet.toml")])
    assert result.exit_code != 0
    assert "extra" in result.stdout.lower() or "extra" in result.stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_check_strict_lint.py -v
```
Expected: FAIL — current `check` does not validate extra.

- [ ] **Step 3: Add the lint to `check.py`**

Edit `src/maldet/commands/check.py` — after manifest is loaded and per-stage symbols resolved, add:
```python
import importlib

from pydantic import BaseModel

# … existing check logic …

def _check_stage_config_class_strict(stage_name: str, dotted: str) -> list[str]:
    """Returns a list of error strings; empty if OK."""
    errors: list[str] = []
    if ":" not in dotted:
        return [f"{stage_name}: config_class must be 'module.sub:Class', got {dotted!r}"]
    mod_name, attr = dotted.split(":", 1)
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:
        return [f"{stage_name}: cannot import {mod_name!r}: {e}"]
    cls = getattr(mod, attr, None)
    if cls is None:
        return [f"{stage_name}: {mod_name} has no attribute {attr!r}"]
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        errors.append(f"{stage_name}: {dotted} is not a pydantic.BaseModel subclass")
        return errors
    if cls.model_config.get("extra") != "forbid":
        errors.append(
            f"{stage_name}: {dotted} must set model_config = ConfigDict(extra='forbid')"
        )
    return errors

# In the main check function, iterate stages and accumulate errors:
for stage_name, stage_spec in manifest.stages.items():
    errors.extend(_check_stage_config_class_strict(stage_name, stage_spec.config_class))

if errors:
    for e in errors:
        typer.echo(e, err=True)
    raise typer.Exit(1)
```

(The engineer should integrate this into the existing `check` flow without losing the other validations.)

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_check_strict_lint.py tests/ -v
```
Expected: new test passes; older check tests may need their manifest fixtures updated to use `extra='forbid'` on any stub Pydantic class.

- [ ] **Step 5: Commit**

```bash
git add src/maldet/commands/check.py tests/test_check_strict_lint.py
git commit -m "feat(check): lint stage config_class for extra='forbid'"
```

---

## Task 1.6: scaffold templates carry the new contract

**Files:**
- Create: `src/maldet/templates/sklearn_basic/configs.py.j2`
- Modify: `src/maldet/templates/sklearn_basic/maldet.toml.j2`
- Create: `src/maldet/templates/lightning_cnn/configs.py.j2`
- Modify: `src/maldet/templates/lightning_cnn/maldet.toml.j2`
- Test: `tests/test_scaffold_templates.py` (extend if exists, else new)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scaffold_templates.py` (or create):
```python
def test_scaffold_sklearn_template_emits_strict_configs(tmp_path):
    """Scaffolded sklearn_basic detector has Pydantic configs with extra='forbid'."""
    from typer.testing import CliRunner
    from maldet.cli import app
    runner = CliRunner()
    out = tmp_path / "newdet"
    result = runner.invoke(app, ["scaffold", "--template", "sklearn_basic", "--name", "newdet", "--out", str(out)])
    assert result.exit_code == 0, result.stdout
    text = (out / "src" / "newdet" / "configs.py").read_text()
    assert "extra='forbid'" in text or 'extra="forbid"' in text
    assert "TrainConfig" in text


def test_scaffold_sklearn_template_emits_config_class_in_manifest(tmp_path):
    from typer.testing import CliRunner
    from maldet.cli import app
    runner = CliRunner()
    out = tmp_path / "newdet2"
    result = runner.invoke(app, ["scaffold", "--template", "sklearn_basic", "--name", "newdet2", "--out", str(out)])
    assert result.exit_code == 0
    toml_text = (out / "maldet.toml").read_text()
    assert "config_class" in toml_text
    assert "newdet2.configs:TrainConfig" in toml_text
```

(Same shape for the lightning_cnn template.)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_scaffold_templates.py -v
```
Expected: FAIL — scaffolded files lack the new structure.

- [ ] **Step 3: Update scaffold templates**

Create `src/maldet/templates/sklearn_basic/configs.py.j2`:
```python
"""Auto-scaffolded Pydantic config classes for {{ name }}."""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainConfig(_Strict):
    n_estimators: int = Field(default=100, ge=1)
    max_depth: int | None = None
    random_state: int = 42


class EvaluateConfig(_Strict):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class PredictConfig(_Strict):
    batch_size: int = Field(default=256, ge=1)
```

Modify `src/maldet/templates/sklearn_basic/maldet.toml.j2` so each `[stages.{stage}]` block includes:
```
config_class = "{{ name }}.configs:TrainConfig"
params_schema = {}
```
(The empty `params_schema = {}` is a placeholder; `maldet build` will populate it via introspect-schema. Never ship this empty in a release.)

Same for `lightning_cnn` template (CNN-specific fields):
```python
class TrainConfig(_Strict):
    epochs: int = Field(default=10, ge=1)
    batch_size: int = Field(default=32, ge=1)
    lr: float = Field(default=1e-3, gt=0.0)
    embed_dim: int = Field(default=128, ge=1)
    hidden_dim: int = Field(default=256, ge=1)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_scaffold_templates.py -v
uv run pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/maldet/templates/ tests/test_scaffold_templates.py
git commit -m "feat(scaffold): templates emit Pydantic configs with extra='forbid' and config_class manifest field"
```

---

## Task 1.7: bump version, CHANGELOG, tag, release

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version**

Edit `pyproject.toml` (or `src/maldet/_version.py` if dynamic version is keyed there). Find the line `__version__ = "1.0.8"` (or equivalent) and change to:
```python
__version__ = "1.1.0"
```

- [ ] **Step 2: Add CHANGELOG entry**

Prepend to `CHANGELOG.md`:
```markdown
## 1.1.0 — 2026-04-27

### BREAKING

- `manifest.stages.{stage}` now requires `config_class` (import path to a Pydantic `BaseModel` subclass) and `params_schema` (JSON Schema). Manifests built with maldet ≤ 1.0 are rejected; rebuild detectors with maldet 1.1.

### Added

- `maldet introspect-schema --config-class …` — auto-derives JSON Schema from a stage's Pydantic config class via Pydantic v2 `model_json_schema()`.
- `confusion_matrix` event kind; `BinaryClassification.evaluate` emits it after metrics.
- `maldet check` lint: every `stage.config_class` must be a `pydantic.BaseModel` subclass with `model_config = ConfigDict(extra="forbid")`.

### Migration

Detector authors:
1. Define Pydantic config classes per stage (`extra="forbid"`).
2. In `maldet.toml` each `[stages.{stage}]` set `config_class = "package.configs:MyConfig"`.
3. Update build pipeline / CI to call `maldet build` which populates `params_schema` automatically.
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest --cov=src/maldet -q
```
Expected: 80%+ coverage; all tests pass.

- [ ] **Step 4: Tag and push**

```bash
git add pyproject.toml CHANGELOG.md src/maldet/_version.py
git commit -m "chore(release): maldet 1.1.0"
git tag v1.1.0
git push origin main
git push origin v1.1.0
```

- [ ] **Step 5: Verify PyPI release**

```bash
sleep 60   # wait for GitHub Actions / publish workflow
pip index versions maldet 2>&1 | grep 1.1.0
```
Expected: `1.1.0` appears.

If the release workflow hasn't been triggered automatically, run the manual publish (`uv build && uv publish` or whatever the project uses).

---

# Batch 2 — elfrfdet 3.0.0

> Working dir: `/home/bolin8017/Documents/repositories/elfrfdet`

## Task 2.1: Define Pydantic config classes

**Files:**
- Create: `src/elfrfdet/configs.py`
- Test: `tests/test_configs.py`

- [ ] **Step 1: Write the failing test**

`tests/test_configs.py`:
```python
"""Pydantic config classes for elfrfdet stages — used by maldet 1.1 introspect-schema."""

import pytest
from pydantic import ValidationError

from elfrfdet.configs import EvaluateConfig, PredictConfig, TrainConfig


def test_train_config_defaults():
    cfg = TrainConfig()
    assert cfg.n_estimators == 100
    assert cfg.max_depth is None
    assert cfg.random_state == 42


def test_train_config_rejects_extras():
    with pytest.raises(ValidationError):
        TrainConfig(unknown_field=1)


def test_train_config_rejects_zero_n_estimators():
    with pytest.raises(ValidationError):
        TrainConfig(n_estimators=0)


def test_evaluate_config_threshold_range():
    EvaluateConfig(threshold=0.0)
    EvaluateConfig(threshold=1.0)
    with pytest.raises(ValidationError):
        EvaluateConfig(threshold=-0.01)


def test_predict_config_defaults():
    PredictConfig()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/bolin8017/Documents/repositories/elfrfdet
uv run pytest tests/test_configs.py -v
```
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create configs.py**

`src/elfrfdet/configs.py`:
```python
"""Pydantic config classes for elfrfdet stages.

These classes are the typed contract between user-supplied params and the
detector. ``maldet introspect-schema`` derives a JSON Schema from each at
build time and embeds it in the manifest.
"""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainConfig(_Strict):
    n_estimators: int = Field(default=100, ge=1, description="Number of trees in the forest.")
    max_depth: int | None = Field(default=None, ge=1, description="Maximum tree depth; None = unlimited.")
    random_state: int = Field(default=42, description="Seed for reproducibility.")


class EvaluateConfig(_Strict):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Decision threshold.")


class PredictConfig(_Strict):
    batch_size: int = Field(default=256, ge=1, description="Prediction batch size.")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_configs.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/elfrfdet/configs.py tests/test_configs.py
git commit -m "feat(configs): add Pydantic stage configs for phase 11e contract"
```

---

## Task 2.2: Update `maldet.toml` for phase 11e contract

**Files:**
- Modify: `maldet.toml`
- Test: `tests/test_manifest_v3.py` (new)

- [ ] **Step 1: Write the failing test**

`tests/test_manifest_v3.py`:
```python
"""Manifest carries config_class for each stage; introspect-schema produces a real schema."""

import json
import subprocess
from pathlib import Path


def test_manifest_has_config_class_for_train():
    import tomllib
    text = Path("maldet.toml").read_bytes()
    parsed = tomllib.loads(text.decode("utf-8"))
    assert parsed["detector"]["version"] == "3.0.0"
    assert parsed["stages"]["train"]["config_class"] == "elfrfdet.configs:TrainConfig"
    assert parsed["stages"]["evaluate"]["config_class"] == "elfrfdet.configs:EvaluateConfig"
    assert parsed["stages"]["predict"]["config_class"] == "elfrfdet.configs:PredictConfig"


def test_introspect_schema_for_train_config_returns_valid_schema(tmp_path):
    import shutil
    out = tmp_path / "train_schema.json"
    res = subprocess.run(
        [
            "uv", "run", "maldet", "introspect-schema",
            "--config-class", "elfrfdet.configs:TrainConfig",
            "--out", str(out),
        ],
        capture_output=True, text=True, cwd=Path.cwd(),
    )
    assert res.returncode == 0, res.stderr
    schema = json.loads(out.read_text())
    assert schema.get("additionalProperties") is False
    assert "n_estimators" in schema["properties"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_manifest_v3.py -v
```
Expected: FAIL — version is still 2.0.6, no config_class in manifest yet.

- [ ] **Step 3: Update `maldet.toml`**

Edit `maldet.toml`:
- Set `[detector] version = "3.0.0"`
- Add to `[stages.train]`:
  ```toml
  config_class = "elfrfdet.configs:TrainConfig"
  params_schema = {}     # populated by `maldet build`
  ```
- Same for `[stages.evaluate]` and `[stages.predict]` with their respective config classes.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add maldet.toml tests/test_manifest_v3.py
git commit -m "feat(manifest): wire config_class for each stage; bump 3.0.0"
```

---

## Task 2.3: Bump maldet pin, run check, tag v3.0.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump maldet pin**

Edit `pyproject.toml`:
```toml
[project]
dependencies = [
    "maldet[mlflow]>=1.1,<2.0",
    # … other deps
]
```

- [ ] **Step 2: Re-resolve + run lint**

```bash
uv lock
uv sync
uv run maldet check
```
Expected: `maldet check` passes (because we already updated configs to `extra="forbid"` in Task 2.1).

- [ ] **Step 3: Add CHANGELOG entry**

Prepend to `CHANGELOG.md`:
```markdown
## 3.0.0 — 2026-04-27

### BREAKING

- Bumped maldet to 1.1 (typed manifest contract).
- Added Pydantic config classes (`TrainConfig`, `EvaluateConfig`, `PredictConfig`) at `elfrfdet.configs`. `maldet.toml` now references them via `[stages.{stage}].config_class`. `params_schema` is auto-derived at `maldet build` time.
- Detectors built with maldet ≤ 1.0 will fail validation under lolday phase11e. Rebuild against this tag.
```

- [ ] **Step 4: Run all tests + commit**

```bash
uv run pytest -q
git add pyproject.toml CHANGELOG.md uv.lock
git commit -m "chore(release): elfrfdet 3.0.0 — phase 11e contract"
git tag v3.0.0
git push origin main
git push origin v3.0.0
```

- [ ] **Step 5: Verify GitHub Actions / lolday detector build trigger**

(Detector image is built by the lolday backend pipeline, not GitHub Actions. The image build is triggered later in Batch 6.)

---

# Batch 3 — elfcnndet 3.0.0

> Working dir: `/home/bolin8017/Documents/repositories/elfcnndet`
> Same shape as Batch 2; only Lightning-specific config fields differ.

## Task 3.1: Define Pydantic config classes

**Files:**
- Create: `src/elfcnndet/configs.py`
- Test: `tests/test_configs.py`

- [ ] **Step 1: Write the failing test**

`tests/test_configs.py`:
```python
import pytest
from pydantic import ValidationError

from elfcnndet.configs import EvaluateConfig, PredictConfig, TrainConfig


def test_train_config_defaults():
    cfg = TrainConfig()
    assert cfg.epochs == 10
    assert cfg.batch_size == 32
    assert cfg.lr == 1e-3
    assert cfg.embed_dim == 128
    assert cfg.hidden_dim == 256


def test_train_config_rejects_zero_epochs():
    with pytest.raises(ValidationError):
        TrainConfig(epochs=0)


def test_train_config_rejects_negative_lr():
    with pytest.raises(ValidationError):
        TrainConfig(lr=-1e-3)


def test_train_config_rejects_extras():
    with pytest.raises(ValidationError):
        TrainConfig(unknown=1)


def test_evaluate_predict_configs_have_defaults():
    EvaluateConfig()
    PredictConfig()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/bolin8017/Documents/repositories/elfcnndet
uv run pytest tests/test_configs.py -v
```
Expected: FAIL — module missing.

- [ ] **Step 3: Create configs.py**

`src/elfcnndet/configs.py`:
```python
"""Pydantic config classes for elfcnndet stages."""

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainConfig(_Strict):
    epochs: int = Field(default=10, ge=1)
    batch_size: int = Field(default=32, ge=1)
    lr: float = Field(default=1e-3, gt=0.0)
    embed_dim: int = Field(default=128, ge=1)
    hidden_dim: int = Field(default=256, ge=1)
    patience: int = Field(default=5, ge=1, description="EarlyStopping patience.")
    random_state: int = 42


class EvaluateConfig(_Strict):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class PredictConfig(_Strict):
    batch_size: int = Field(default=256, ge=1)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_configs.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/elfcnndet/configs.py tests/test_configs.py
git commit -m "feat(configs): add Pydantic stage configs"
```

---

## Task 3.2: Update `maldet.toml` for phase 11e

(Same template as Task 2.2; only the import path changes from `elfrfdet.*` to `elfcnndet.*`.)

- [ ] **Step 1: Write failing test** (`tests/test_manifest_v3.py`) — assert version 3.0.0 + `config_class` per stage points at `elfcnndet.configs:*`.
- [ ] **Step 2: Run test → FAIL.**
- [ ] **Step 3: Edit `maldet.toml`** — bump version, add `config_class` and `params_schema = {}` to each stage block.
- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** — message: `feat(manifest): wire config_class for each stage; bump 3.0.0`.

---

## Task 3.3: Bump maldet pin, run check, tag v3.0.0

(Identical to Task 2.3; pin string is `maldet[lightning,mlflow]>=1.1,<2.0`. CHANGELOG entry mirrors Batch 2's.)

- [ ] **Step 1: Bump pin in `pyproject.toml`** to `maldet[lightning,mlflow]>=1.1,<2.0`.
- [ ] **Step 2: Run `uv lock && uv sync && uv run maldet check`** — expect green.
- [ ] **Step 3: Prepend CHANGELOG 3.0.0 entry.**
- [ ] **Step 4: Run all tests → commit → tag v3.0.0 → push.**
- [ ] **Step 5: Confirm GitHub tag visible on `bolin8017/elfcnndet`.**

---

# Batch 4 — Lolday backend phase11e

> Working dir: `/home/bolin8017/Documents/repositories/lolday/backend`

## Task 4.1: Expose `manifest` on `VersionDetailRead`

**Files:**
- Modify: `app/schemas/detector.py`
- Test: `tests/test_schemas_version_detail_read.py` (new)

- [ ] **Step 1: Write the failing test**

`tests/test_schemas_version_detail_read.py`:
```python
"""VersionDetailRead exposes the full manifest for typed-form rendering."""

from app.schemas.detector import VersionDetailRead


def test_version_detail_read_has_manifest_field():
    fields = VersionDetailRead.model_fields
    assert "manifest" in fields


def test_version_detail_read_serializes_manifest():
    import datetime as _dt
    import uuid as _uuid

    payload = {
        "id": _uuid.uuid4(),
        "git_tag": "v3.0.0",
        "git_sha": "0" * 40,
        "harbor_image": "harbor.example/x:v3.0.0",
        "image_digest": "sha256:abc",
        "built_at": _dt.datetime.now(_dt.timezone.utc),
        "status": "active",
        "manifest": {
            "detector": {"name": "x", "version": "3.0.0"},
            "stages": {"train": {"config_class": "x.configs:TrainConfig", "params_schema": {"type": "object"}}},
        },
    }
    obj = VersionDetailRead.model_validate(payload)
    assert obj.manifest["stages"]["train"]["config_class"] == "x.configs:TrainConfig"
```

- [ ] **Step 2: Run test → FAIL.**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run pytest tests/test_schemas_version_detail_read.py -v
```

- [ ] **Step 3: Add the field**

Edit `app/schemas/detector.py`:
```python
from typing import Any

class VersionDetailRead(VersionRead):
    manifest: dict[str, Any]   # phase 11e
```

- [ ] **Step 4: Run test → PASS.**

```bash
uv run pytest tests/test_schemas_version_detail_read.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/schemas/detector.py tests/test_schemas_version_detail_read.py
git commit -m "feat(schemas): expose manifest on VersionDetailRead"
```

---

## Task 4.2: Expose `summary_metrics` on `JobSummary`

**Files:**
- Modify: `app/schemas/job.py`
- Test: extend `tests/test_jobs_*.py` or new `tests/test_schemas_job_summary.py`

- [ ] **Step 1: Write the failing test**

`tests/test_schemas_job_summary.py`:
```python
from app.schemas.job import JobSummary


def test_job_summary_has_summary_metrics_field():
    assert "summary_metrics" in JobSummary.model_fields


def test_job_summary_summary_metrics_optional():
    import datetime as _dt
    import uuid as _uuid
    obj = JobSummary.model_validate({
        "id": _uuid.uuid4(),
        "type": "train",
        "status": "succeeded",
        "submitted_at": _dt.datetime.now(_dt.timezone.utc),
        "summary_metrics": None,
    })
    assert obj.summary_metrics is None

    obj2 = JobSummary.model_validate({
        "id": _uuid.uuid4(),
        "type": "train",
        "status": "succeeded",
        "submitted_at": _dt.datetime.now(_dt.timezone.utc),
        "summary_metrics": {"metrics": {"acc": 0.9}, "confusion_matrix": None},
    })
    assert obj2.summary_metrics["metrics"]["acc"] == 0.9
```

- [ ] **Step 2: Run test → FAIL.**

```bash
uv run pytest tests/test_schemas_job_summary.py -v
```

- [ ] **Step 3: Add the field**

Edit `app/schemas/job.py` `JobSummary`:
```python
from typing import Any

class JobSummary(BaseModel):
    # … existing fields …
    summary_metrics: dict[str, Any] | None = None
```

- [ ] **Step 4: Run test → PASS.**

- [ ] **Step 5: Commit**

```bash
git add app/schemas/job.py tests/test_schemas_job_summary.py
git commit -m "feat(schemas): expose summary_metrics on JobSummary"
```

---

## Task 4.3: jsonschema-based `validate_user_params`

**Files:**
- Create: `app/services/jobs_params_validate.py`
- Delete: `app/services/jobs_params_guard.py`
- Delete: `tests/test_services_jobs_params_guard.py`
- Modify: `app/routers/jobs.py`
- Test: `tests/test_jsonschema_validate_params.py` (new)

- [ ] **Step 1: Write the failing test**

`tests/test_jsonschema_validate_params.py`:
```python
"""jsonschema-based user params validation — replaces phase 11c hand-rolled guard."""

import pytest

from app.services.jobs_params_validate import (
    UserParamsRejected,
    validate_user_params,
)


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "n_estimators": {"type": "integer", "minimum": 1},
        "lr": {"type": "number", "exclusiveMinimum": 0.0},
    },
    "required": [],
}


def test_valid_params_pass():
    validate_user_params(params={"n_estimators": 100, "lr": 0.01}, schema=SCHEMA)


def test_extra_field_rejected_with_pointer():
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"unknown": 1}, schema=SCHEMA)
    assert "unknown" in str(ei.value)


def test_type_mismatch_rejected_with_pointer():
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"n_estimators": "many"}, schema=SCHEMA)
    assert "/n_estimators" in str(ei.value)


def test_out_of_range_rejected_with_pointer():
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"n_estimators": 0}, schema=SCHEMA)
    assert "/n_estimators" in str(ei.value)


def test_empty_params_pass():
    validate_user_params(params={}, schema=SCHEMA)
```

- [ ] **Step 2: Run test → FAIL.**

- [ ] **Step 3: Create the new module**

`app/services/jobs_params_validate.py`:
```python
"""Phase 11e: validate user-supplied job params against the manifest's params_schema.

Replaces the hand-rolled `jobs_params_guard` from Phase 11c. The schema is
derived by maldet from the detector's Pydantic config class at build time.
"""

from __future__ import annotations

from typing import Any

import jsonschema


class UserParamsRejected(ValueError):
    """Raised when user params don't satisfy the stage's JSON Schema."""


def _format_pointer(absolute_path) -> str:
    return "/" + "/".join(str(p) for p in absolute_path) if absolute_path else "/"


def validate_user_params(*, params: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate ``params`` against ``schema`` (JSON Schema Draft 2020-12).

    Raises :class:`UserParamsRejected` with a single string aggregating each
    error's JSON Pointer + message, so the caller can return a 422 with a
    diagnostic the user can act on.
    """
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    detail = "; ".join(
        f"{_format_pointer(e.absolute_path)}: {e.message}" for e in errors
    )
    raise UserParamsRejected(detail)
```

- [ ] **Step 4: Run test → PASS.**

- [ ] **Step 5: Wire into `routers/jobs.py` and delete the old guard**

In `app/routers/jobs.py:create_job`, find the existing `validate_user_params(body.params)` call (or `UserParamsRejected` import) and replace with:
```python
from app.services.jobs_params_validate import (
    UserParamsRejected,
    validate_user_params,
)

# … after manifest_model is loaded …
stage_schema = manifest_model.stages[body.type.value].params_schema
try:
    validate_user_params(params=body.params, schema=stage_schema)
except UserParamsRejected as e:
    raise HTTPException(status_code=422, detail=str(e))
```

Delete:
```bash
git rm app/services/jobs_params_guard.py tests/test_services_jobs_params_guard.py
```

- [ ] **Step 6: Run full backend test suite**

```bash
uv run pytest -q
```
Expected: all pass except the deleted-test file (already removed). Net: ≥ 404 - 1 deleted + 5 new = 408 tests.

- [ ] **Step 7: Add `jsonschema` to backend deps if not already present**

```bash
grep jsonschema pyproject.toml || echo "ADD: jsonschema>=4.20"
# If missing:
uv add 'jsonschema>=4.20,<5'
```

- [ ] **Step 8: Commit**

```bash
git add app/services/jobs_params_validate.py app/routers/jobs.py tests/test_jsonschema_validate_params.py pyproject.toml uv.lock
git commit -m "feat(jobs): jsonschema-based user_params validation; drop hand-rolled guard"
```

---

## Task 4.4: Reconciler projects events into `summary_metrics`

**Files:**
- Modify: `app/reconciler.py`
- Test: `tests/test_reconciler_summary_projection.py` (new)

- [ ] **Step 1: Write the failing test**

`tests/test_reconciler_summary_projection.py`:
```python
"""On stage_end, reconciler aggregates last-per-name metric events into summary_metrics."""

import datetime as _dt
import uuid as _uuid

import pytest

from app.models import Job, JobEvent
from app.reconciler import _project_summary_metrics
from app.models.job import JobStatus, JobType


@pytest.fixture
async def terminal_job(db_session):
    job = Job(
        id=_uuid.uuid4(),
        type=JobType.TRAIN,
        status=JobStatus.SUCCEEDED,
        owner_id=_uuid.uuid4(),
        detector_version_id=_uuid.uuid4(),
    )
    db_session.add(job)
    await db_session.commit()
    return job


@pytest.mark.asyncio
async def test_projection_takes_last_metric_per_name(db_session, terminal_job):
    # Two metric events for the same name — last by ts wins.
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=terminal_job.id, ts=base,
        kind="metric", payload={"name": "train_loss", "value": 1.0, "step": 0},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=terminal_job.id, ts=base + _dt.timedelta(seconds=1),
        kind="metric", payload={"name": "train_loss", "value": 0.1, "step": 5},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=terminal_job.id, ts=base + _dt.timedelta(seconds=2),
        kind="confusion_matrix", payload={"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]},
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, terminal_job.id)
    await db_session.refresh(terminal_job)

    assert terminal_job.summary_metrics == {
        "metrics": {"train_loss": 0.1},
        "confusion_matrix": {"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]},
    }


@pytest.mark.asyncio
async def test_projection_empty_when_no_metric_events(db_session, terminal_job):
    await _project_summary_metrics(db_session, terminal_job.id)
    await db_session.refresh(terminal_job)
    assert terminal_job.summary_metrics == {"metrics": {}, "confusion_matrix": None}


@pytest.mark.asyncio
async def test_projection_idempotent(db_session, terminal_job):
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=terminal_job.id, ts=base,
        kind="metric", payload={"name": "acc", "value": 0.99},
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, terminal_job.id)
    first = dict(terminal_job.summary_metrics)
    await _project_summary_metrics(db_session, terminal_job.id)
    await db_session.refresh(terminal_job)
    assert terminal_job.summary_metrics == first
```

- [ ] **Step 2: Run test → FAIL.**

- [ ] **Step 3: Implement `_project_summary_metrics`**

Append to `app/reconciler.py`:
```python
import logging
from sqlalchemy import select

from app.metrics import BACKEND_ERRORS
from app.models import Job, JobEvent

_log = logging.getLogger(__name__)


async def _project_summary_metrics(session, job_id) -> None:
    """Aggregate last-per-name metric events + latest confusion_matrix event
    for ``job_id`` into ``Job.summary_metrics``. Idempotent."""
    rows = (await session.execute(
        select(JobEvent.kind, JobEvent.payload, JobEvent.ts)
        .where(JobEvent.job_id == job_id)
        .where(JobEvent.kind.in_(["metric", "confusion_matrix"]))
        .order_by(JobEvent.ts.asc())
    )).all()

    metrics: dict[str, float] = {}
    confusion_matrix = None
    for kind, payload, _ts in rows:
        if kind == "metric":
            try:
                metrics[payload["name"]] = float(payload["value"])
            except (KeyError, TypeError, ValueError):
                continue
        elif kind == "confusion_matrix":
            try:
                confusion_matrix = {
                    "labels": payload["labels"],
                    "matrix": payload["matrix"],
                }
            except KeyError:
                continue

    job = await session.get(Job, job_id)
    job.summary_metrics = {"metrics": metrics, "confusion_matrix": confusion_matrix}
    await session.commit()
```

Then in the existing reconciler logic that handles `stage_end` events with a successful or terminal status, call:
```python
try:
    await _project_summary_metrics(session, job.id)
except Exception:  # noqa: BLE001 — never block job termination on cache projection
    BACKEND_ERRORS.labels(stage="summary_projection").inc()
    _log.exception("summary_metrics projection failed", extra={"job_id": str(job.id)})
```

Place this after the existing `notify_job_completed` / `notify_job_failed` dispatch.

- [ ] **Step 4: Run test → PASS.**

```bash
uv run pytest tests/test_reconciler_summary_projection.py -v
uv run pytest tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add app/reconciler.py tests/test_reconciler_summary_projection.py
git commit -m "feat(reconciler): project events into summary_metrics on stage_end"
```

---

## Task 4.5: Backfill script (optional one-shot)

**Files:**
- Create: `scripts/backfill-summary-metrics.py`

- [ ] **Step 1: Write the script**

`scripts/backfill-summary-metrics.py`:
```python
"""Phase 11e one-shot backfill — populate summary_metrics for terminal jobs.

Idempotent. Run after phase 11e backend deploy if the operator wants the
audit-trail jobs from phase 11d to display final metrics.

Usage:
    uv run python scripts/backfill-summary-metrics.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.db import async_session_maker
from app.models import Job
from app.models.job import NON_TERMINAL_STATUSES
from app.reconciler import _project_summary_metrics

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("backfill")


async def main() -> None:
    async with async_session_maker() as session:
        terminal_with_null = (await session.execute(
            select(Job.id).where(
                ~Job.status.in_(NON_TERMINAL_STATUSES),
                Job.summary_metrics.is_(None),
            )
        )).scalars().all()

    _log.info("found %d terminal jobs with null summary_metrics", len(terminal_with_null))
    for jid in terminal_with_null:
        async with async_session_maker() as session:
            try:
                await _project_summary_metrics(session, jid)
                _log.info("projected %s", jid)
            except Exception:
                _log.exception("projection failed for %s", jid)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke run against test DB**

(Test DB has no jobs, so the script logs "found 0 terminal jobs" and exits.)

```bash
uv run python scripts/backfill-summary-metrics.py
```
Expected: clean exit; "found 0 terminal jobs".

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill-summary-metrics.py
git commit -m "feat(scripts): one-shot summary_metrics backfill for phase 11e"
```

---

## Task 4.6: Build + push backend phase11e image

**Files:**
- Modify: `lolday/scripts/deploy.sh` (default tag)

- [ ] **Step 1: Bump default backend tag**

Edit `scripts/deploy.sh`:
```bash
BACKEND_IMAGE=${BACKEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-backend:phase11e}
```

- [ ] **Step 2: Build the image**

```bash
cd /home/bolin8017/Documents/repositories/lolday
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11e backend
```
Expected: build succeeds; image tagged.

- [ ] **Step 3: Push**

```bash
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11e
```
Expected: layers pushed; digest printed.

- [ ] **Step 4: Verify image is in Harbor**

```bash
kubectl -n lolday exec deploy/lolday-harbor-core -- \
  curl -s -u admin:$HARBOR_ADMIN_PASSWORD \
  'http://harbor.lolday.svc/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts?with_tag=true' \
  | head -c 400
```
Expected: response includes `phase11e` tag.

- [ ] **Step 5: Commit deploy.sh change**

```bash
git add scripts/deploy.sh
git commit -m "chore(deploy): bump backend default to phase11e"
```

---

# Batch 5 — Lolday frontend phase11e

> Working dir: `/home/bolin8017/Documents/repositories/lolday/frontend`

## Task 5.1: Regenerate `schema.gen.ts` against deployed phase11e backend

**Files:**
- Modify: `frontend/src/api/schema.gen.ts` (regenerated)

- [ ] **Step 1: Port-forward backend (deploy phase11e first if not yet)**

If backend phase11e isn't deployed yet, deploy it now via Task 4.6 + a quick `helm upgrade --reuse-values --set backend.image=…:phase11e`. Otherwise:

```bash
kubectl port-forward -n lolday deploy/backend 18000:8000 > /tmp/pf-backend.log 2>&1 &
sleep 3
curl -s http://localhost:18000/openapi.json | head -c 200
```
Expected: openapi.json fetched.

- [ ] **Step 2: Regenerate**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
SCHEMA_URL=http://localhost:18000/openapi.json bash scripts/gen-api-types.sh
```
Expected: "Generated src/api/schema.gen.ts".

- [ ] **Step 3: Verify new fields present**

```bash
grep -E '"manifest"|summary_metrics' src/api/schema.gen.ts | head -10
```
Expected: hits in both `VersionDetailRead` and `JobSummary`.

- [ ] **Step 4: Run typecheck — expect surfaced regressions**

```bash
pnpm typecheck
```
Expected: passes (the next tasks add new code that uses the new fields; they shouldn't break existing).

- [ ] **Step 5: Commit**

```bash
git add src/api/schema.gen.ts
git commit -m "chore(frontend): regen schema.gen.ts for phase11e backend"
```

---

## Task 5.2: `JobSubmitForm` — RJSF replaces JSON textarea

**Files:**
- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/src/components/forms/JobSubmitForm.logic.ts` (drop `parseParams`)
- Modify: `frontend/tests/unit/components/JobSubmitForm.test.tsx` (replace tests)

- [ ] **Step 1: Write the failing test**

Rewrite `tests/unit/components/JobSubmitForm.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { requiredFieldsForType } from "@/components/forms/JobSubmitForm.logic";

describe("requiredFieldsForType", () => {
  it("train needs train+test datasets", () => {
    expect(requiredFieldsForType("train")).toEqual(["train_dataset_id", "test_dataset_id"]);
  });
  it("evaluate needs test+source_model", () => {
    expect(requiredFieldsForType("evaluate")).toEqual(["test_dataset_id", "source_model_version_id"]);
  });
  it("predict needs predict+source_model", () => {
    expect(requiredFieldsForType("predict")).toEqual(["predict_dataset_id", "source_model_version_id"]);
  });
});

// RJSF rendering test would mock useDetectorVersion to return a manifest with
// a sample params_schema, then assert the form fields are rendered. For phase
// 11e initial cut, validate the parseParams helper has been removed:

describe("phase 11e — JSON textarea path removed", () => {
  it("does not export parseParams", async () => {
    const mod = await import("@/components/forms/JobSubmitForm.logic");
    expect(mod).not.toHaveProperty("parseParams");
  });
});
```

(A more complete RJSF rendering test requires mocking `useDetectorVersion` and rendering the full form; see Task 5.6 for the e2e variant. For now the unit test surface guards against accidental textarea regression.)

- [ ] **Step 2: Run tests → FAIL**

```bash
pnpm test -- tests/unit/components/JobSubmitForm.test.tsx
```
Expected: FAIL on `parseParams`-export check (it still exists).

- [ ] **Step 3: Drop `parseParams` from logic**

Edit `src/components/forms/JobSubmitForm.logic.ts` — remove the `parseParams` export and `ParseParamsResult` type. File should only contain `requiredFieldsForType`.

- [ ] **Step 4: Replace JSON textarea with RJSF**

Edit `src/components/forms/JobSubmitForm.tsx`:
```tsx
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useDetectors, useDetectorVersions, useDetectorVersion } from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import { useSubmitJob, useJob, type JobType } from "@/api/queries/jobs";
import { RjsfConfigForm } from "./RjsfConfigForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { requiredFieldsForType } from "./JobSubmitForm.logic";

const TYPES: JobType[] = ["train", "evaluate", "predict"];

export function JobSubmitForm() {
  const [params] = useSearchParams();
  const fromJobId = params.get("from");
  const { data: fromJob } = useJob(fromJobId ?? "");

  const [type, setType] = useState<JobType>("train");
  const [detectorId, setDetectorId] = useState("");
  const [versionTag, setVersionTag] = useState("");
  const [trainDatasetId, setTrainDatasetId] = useState("");
  const [testDatasetId, setTestDatasetId] = useState("");
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [sourceModelName, setSourceModelName] = useState("");
  const [sourceModelVersionId, setSourceModelVersionId] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const { data: detectors } = useDetectors();
  const { data: versions } = useDetectorVersions(detectorId);
  const { data: versionDetail } = useDetectorVersion(detectorId, versionTag);
  const { data: datasets } = useDatasets("all");
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(sourceModelName);

  useEffect(() => {
    if (!fromJob) return;
    setType(fromJob.type as JobType);
    if (fromJob.train_dataset_id) setTrainDatasetId(fromJob.train_dataset_id);
    if (fromJob.test_dataset_id) setTestDatasetId(fromJob.test_dataset_id);
    if (fromJob.predict_dataset_id) setPredictDatasetId(fromJob.predict_dataset_id);
  }, [fromJob]);

  const datasetsArr = ((datasets as { items?: { id: string; name: string }[] })?.items) ?? [];
  const versionsArr = ((versions as { items?: { id: string; git_tag: string; status: string }[] })?.items) ?? [];
  const modelsArr = (models as { name: string }[] | undefined) ?? [];
  const modelVersionsArr = (modelVersions as { items?: { id: string; mlflow_version: number; current_stage: string }[] })?.items ?? [];

  const stageSchema = (versionDetail as any)?.manifest?.stages?.[type]?.params_schema;

  const mut = useSubmitJob();
  const nav = useNavigate();

  const canSubmit = (() => {
    if (!detectorId || !versionTag) return false;
    const need = requiredFieldsForType(type);
    if (need.includes("train_dataset_id") && !trainDatasetId) return false;
    if (need.includes("test_dataset_id") && !testDatasetId) return false;
    if (need.includes("predict_dataset_id") && !predictDatasetId) return false;
    if (need.includes("source_model_version_id") && !sourceModelVersionId) return false;
    return true;
  })();

  async function submit() {
    setSubmitError(null);
    const versionId = versionsArr.find((v) => v.git_tag === versionTag)?.id;
    if (!versionId) return;
    try {
      const job = await mut.mutateAsync({
        type,
        detector_version_id: versionId,
        train_dataset_id: type === "train" ? trainDatasetId : null,
        test_dataset_id: ["train", "evaluate"].includes(type) ? testDatasetId : null,
        predict_dataset_id: type === "predict" ? predictDatasetId : null,
        source_model_version_id: ["evaluate", "predict"].includes(type) ? sourceModelVersionId : null,
        params: config,
      } as unknown as import("@/api/schema.gen").components["schemas"]["JobCreate"]);
      nav(`/jobs/${job.id}`);
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail ?? "Submit failed");
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Job type, Detector, Data cards unchanged from phase 11d.1 */}
      {/* …copy from existing JobSubmitForm.tsx… */}

      <Card>
        <CardHeader><CardTitle>Hyperparameters</CardTitle></CardHeader>
        <CardContent>
          {stageSchema ? (
            <RjsfConfigForm schema={stageSchema as object} value={config} onChange={setConfig} />
          ) : versionTag ? (
            <p className="text-sm text-destructive">
              Selected detector version has no params schema; rebuild with maldet ≥ 1.1.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">Pick a detector + version to load its hyperparameter form.</p>
          )}
        </CardContent>
      </Card>

      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => nav(-1)}>Cancel</Button>
        <Button disabled={!canSubmit || mut.isPending} onClick={submit}>Submit job</Button>
      </div>
    </div>
  );
}
```

(Engineer should preserve the other Cards/UI from the existing file; only the Hyperparameters Card changes.)

- [ ] **Step 5: Run tests + typecheck**

```bash
pnpm test
pnpm typecheck
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/components/forms/JobSubmitForm.tsx src/components/forms/JobSubmitForm.logic.ts tests/unit/components/JobSubmitForm.test.tsx
git commit -m "feat(jobs): RJSF form replaces JSON textarea using manifest params_schema"
```

---

## Task 5.3: Detector page — restore "View manifest" sheet

**Files:**
- Modify: `frontend/src/routes/_authed.detectors.$id.tsx`

- [ ] **Step 1: Re-add the column action + sheet**

Edit `_authed.detectors.$id.tsx`:
1. Re-import: `JsonViewer`, `useDetectorVersion`.
2. Re-introduce `openSchemaTag` state (rename to `openManifestTag`).
3. Add a `View manifest` button cell to the versions table.
4. Add the `Sheet` block showing `<JsonViewer value={data.manifest} />`.

Code:
```tsx
import { useDetectorVersion } from "@/api/queries/detectors";
import { JsonViewer } from "@/components/common/JsonViewer";

// Inside the component:
const [openManifestTag, setOpenManifestTag] = useState<string | null>(null);

// In versionsCols add an actions column:
{
  id: "actions",
  header: "",
  cell: ({ row }) => (
    <Button variant="ghost" size="sm" onClick={() => setOpenManifestTag(row.original.tag)}>
      View manifest
    </Button>
  ),
}

// Inside <TabsContent value="versions">:
<Sheet open={!!openManifestTag} onOpenChange={(o) => !o && setOpenManifestTag(null)}>
  <SheetContent className="w-[760px] sm:max-w-[800px]">
    <SheetHeader><SheetTitle>Manifest: {openManifestTag}</SheetTitle></SheetHeader>
    <div className="mt-4">
      {openManifestTag && <ManifestView detectorId={id} tag={openManifestTag} />}
    </div>
  </SheetContent>
</Sheet>

// Bottom of file — restore the helper:
function ManifestView({ detectorId, tag }: { detectorId: string; tag: string }) {
  const { data } = useDetectorVersion(detectorId, tag);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  return <JsonViewer value={(data as { manifest?: unknown }).manifest} />;
}
```

- [ ] **Step 2: Run typecheck + tests**

```bash
pnpm typecheck
pnpm test
```
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add src/routes/_authed.detectors.\$id.tsx
git commit -m "feat(detectors): restore View manifest sheet (manifest is now the typed contract)"
```

---

## Task 5.4: Job list — Final metrics tile column

**Files:**
- Modify: `frontend/src/routes/_authed.jobs.tsx` (or whatever the list route is)
- Test: `frontend/tests/unit/components/JobsList.test.tsx` (new)

- [ ] **Step 1: Find the list route**

```bash
grep -rln 'useJobs' src/routes/ | head -3
```
Identify the file (likely `_authed.jobs.tsx` or `_authed.jobs._index.tsx`).

- [ ] **Step 2: Write the failing test**

`tests/unit/components/JobsList.test.tsx` (or a focused test on a tile component):
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";

describe("FinalMetricsTile", () => {
  it("renders dash when summary_metrics is null", () => {
    render(<FinalMetricsTile summaryMetrics={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders first two metrics + +N more", () => {
    render(<FinalMetricsTile summaryMetrics={{
      metrics: { acc: 0.987, f1: 0.94, precision: 0.99, recall: 0.92 },
      confusion_matrix: null,
    }} />);
    expect(screen.getByText(/acc:/)).toBeInTheDocument();
    expect(screen.getByText(/f1:/)).toBeInTheDocument();
    expect(screen.getByText(/\+2/)).toBeInTheDocument();
  });

  it("renders all metrics inline if 2 or fewer", () => {
    render(<FinalMetricsTile summaryMetrics={{
      metrics: { acc: 0.99 },
      confusion_matrix: null,
    }} />);
    expect(screen.queryByText(/\+\d/)).toBeNull();
  });
});
```

- [ ] **Step 3: Run test → FAIL.**

```bash
pnpm test -- tests/unit/components/JobsList.test.tsx
```
Expected: FAIL — component does not exist.

- [ ] **Step 4: Create the component**

`src/components/jobs/FinalMetricsTile.tsx`:
```tsx
type SummaryMetrics = {
  metrics?: Record<string, number>;
  confusion_matrix?: unknown;
};

export function FinalMetricsTile({
  summaryMetrics,
}: {
  summaryMetrics: SummaryMetrics | null | undefined;
}) {
  const metrics = summaryMetrics?.metrics ?? {};
  const entries = Object.entries(metrics);
  if (entries.length === 0) return <span className="text-muted-foreground">—</span>;
  const shown = entries.slice(0, 2);
  const more = entries.length - shown.length;
  return (
    <div className="flex gap-1 text-xs">
      {shown.map(([k, v]) => (
        <span key={k} className="rounded border px-1">
          {k}: {Number(v).toFixed(3)}
        </span>
      ))}
      {more > 0 && <span className="text-muted-foreground">+{more}</span>}
    </div>
  );
}
```

- [ ] **Step 5: Wire into the list route**

In the jobs list route file, add a column to the table:
```tsx
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";

// In columns:
{
  id: "final_metrics",
  header: "Final metrics",
  cell: ({ row }) => <FinalMetricsTile summaryMetrics={row.original.summary_metrics} />,
}
```

- [ ] **Step 6: Run tests + typecheck**

```bash
pnpm test
pnpm typecheck
```

- [ ] **Step 7: Commit**

```bash
git add src/components/jobs/FinalMetricsTile.tsx tests/unit/components/JobsList.test.tsx src/routes/_authed.jobs.tsx
git commit -m "feat(jobs): Final metrics tile column on /jobs list"
```

---

## Task 5.5: Live metrics chart visibility — `hasTimeSeries`

**Files:**
- Modify: `frontend/src/routes/_authed.jobs.$id.tsx`

- [ ] **Step 1: Compute `hasTimeSeries` and gate the chart**

Edit `_authed.jobs.$id.tsx`. After `const { events, error: eventsError } = useJobEvents(id, isLive);`:
```tsx
const hasTimeSeries = events.some(
  (e) => e.kind === "metric" && typeof e.step === "number" && e.step >= 1,
);
```

Replace the existing `(events.length > 0 || eventsError) && …` chart-card guard with:
```tsx
{(hasTimeSeries || eventsError) && (
  <Card>
    <CardHeader><CardTitle>Live metrics</CardTitle></CardHeader>
    <CardContent>
      {eventsError && <p className="text-sm text-destructive">{eventsError}</p>}
      {hasTimeSeries && <JobMetricChart events={events} />}
    </CardContent>
  </Card>
)}
```

- [ ] **Step 2: Run typecheck + tests**

```bash
pnpm typecheck
pnpm test
```

- [ ] **Step 3: Commit**

```bash
git add src/routes/_authed.jobs.\$id.tsx
git commit -m "feat(jobs): hide Live metrics card unless events have step ≥ 1"
```

---

## Task 5.6: Phase 11e e2e smoke spec (opt-in)

**Files:**
- Create: `frontend/tests/e2e/phase11e-full-flow.spec.ts`

- [ ] **Step 1: Write the spec**

```tsx
/**
 * Phase 11e production smoke test.
 *
 * Opt-in: set `PHASE11E_VERIFY=1` and source `~/.lolday-cf-svctoken.env`.
 * Drives the full RJSF → submit → reconciler → list-page tile flow against
 * the deployed cluster.
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.PHASE11E_VERIFY === "1";
const DETECTOR_NAME = process.env.PHASE11E_DETECTOR ?? "elfrfdet";
const DETECTOR_TAG = process.env.PHASE11E_DETECTOR_TAG ?? "v3.0.0";

test.use({
  baseURL: "https://lolday.connlabai.com",
  ignoreHTTPSErrors: true,
  extraHTTPHeaders: {
    "CF-Access-Client-Id": process.env.CF_ACCESS_CLIENT_ID ?? "",
    "CF-Access-Client-Secret": process.env.CF_ACCESS_CLIENT_SECRET ?? "",
  },
  launchOptions: { args: [] },
});

test("phase 11e — RJSF → submit → list-page tile", async ({ page }) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");
  test.setTimeout(120_000);

  await page.goto("/jobs/new", { waitUntil: "domcontentloaded" });

  // Pick detector + version
  await page.getByText(/^Detector$/).locator("..").getByRole("combobox").click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_NAME) }).click();
  await page.getByText(/^Version$/).locator("..").getByRole("combobox").click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_TAG) }).click();

  // Confirm RJSF rendered (look for a known field name)
  await expect(page.getByLabel(/n_estimators|epochs/i)).toBeVisible({ timeout: 10_000 });

  // Smoke only — not actually submitting (would consume cluster resources)
  await page.screenshot({ path: "/tmp/phase11e-rjsf.png" });
});

test("phase 11e — list page renders Final metrics tile for terminal jobs", async ({ page }) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");
  await page.goto("/jobs", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Final metrics")).toBeVisible({ timeout: 10_000 });
});
```

- [ ] **Step 2: Run smoke (deferred until cluster has phase11e backend + v3.0.0 detector)**

```bash
source ~/.lolday-cf-svctoken.env
PHASE11E_VERIFY=1 E2E_BASE_URL=https://lolday.connlabai.com pnpm exec playwright test phase11e-full-flow --reporter=line
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/phase11e-full-flow.spec.ts
git commit -m "test(e2e): phase 11e full-flow smoke spec (opt-in)"
```

---

## Task 5.7: Build + push frontend phase11e image

**Files:**
- Modify: `lolday/scripts/deploy.sh` (default tag)

- [ ] **Step 1: Bump default tag**

Edit `scripts/deploy.sh`:
```bash
FRONTEND_IMAGE=${FRONTEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-frontend:phase11e}
```

- [ ] **Step 2: Build**

```bash
cd /home/bolin8017/Documents/repositories/lolday
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase11e frontend
```

- [ ] **Step 3: Push**

```bash
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase11e
```

- [ ] **Step 4: Commit**

```bash
git add scripts/deploy.sh
git commit -m "chore(deploy): bump frontend default to phase11e"
```

---

# Batch 6 — Deploy + verify

## Task 6.1: Trigger detector v3.0.0 builds

**Files:** none

- [ ] **Step 1: POST to lolday backend (using service token) to trigger build for elfrfdet v3.0.0**

```bash
source ~/.lolday-cf-svctoken.env
DETECTOR_ID_RFDET=$(curl -s \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  https://lolday.connlabai.com/api/v1/detectors | jq -r '.items[] | select(.name=="elfrfdet") | .id')

curl -s -X POST \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"git_tag":"v3.0.0"}' \
  "https://lolday.connlabai.com/api/v1/detectors/$DETECTOR_ID_RFDET/builds"
```
Expected: build row created.

- [ ] **Step 2: Same for elfcnndet v3.0.0**

```bash
DETECTOR_ID_CNNDET=$(curl -s \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  https://lolday.connlabai.com/api/v1/detectors | jq -r '.items[] | select(.name=="elfcnndet") | .id')

curl -s -X POST \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"git_tag":"v3.0.0"}' \
  "https://lolday.connlabai.com/api/v1/detectors/$DETECTOR_ID_CNNDET/builds"
```

- [ ] **Step 3: Wait for both builds → succeeded**

Poll:
```bash
watch -n 10 'curl -s -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" "https://lolday.connlabai.com/api/v1/detectors/$DETECTOR_ID_RFDET/builds" | jq ".items[] | select(.git_tag==\"v3.0.0\") | .status"'
```
Expected: `"succeeded"` for both, ~10-15 minutes each.

- [ ] **Step 4: Verify manifest in DB**

```bash
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -tAc \
  "SELECT manifest->'stages'->'train'->>'config_class' FROM detector_version WHERE git_tag='v3.0.0' ORDER BY built_at DESC LIMIT 5;"
```
Expected: rows with `elfrfdet.configs:TrainConfig` or `elfcnndet.configs:TrainConfig`.

```bash
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -tAc \
  "SELECT (manifest->'stages'->'train'->'params_schema') IS NOT NULL FROM detector_version WHERE git_tag='v3.0.0';"
```
Expected: `t` (true) for both rows.

- [ ] **Step 5: Save build IDs for reference (optional)**

```bash
echo "elfrfdet v3.0.0 detector_version_id: $(...)" >> /tmp/phase11e-builds.txt
```

---

## Task 6.2: Helm upgrade with both phase11e images

- [ ] **Step 1: Single-transaction helm upgrade**

```bash
helm -n lolday upgrade lolday /home/bolin8017/Documents/repositories/lolday/charts/lolday \
  --reuse-values \
  --set backend.image=harbor.lolday.svc:80/lolday/lolday-backend:phase11e \
  --set frontend.image=harbor.lolday.svc:80/lolday/lolday-frontend:phase11e \
  --wait --timeout 5m
```
Expected: `STATUS: deployed`; new helm rev printed.

- [ ] **Step 2: Verify rollout**

```bash
kubectl -n lolday rollout status deploy/backend --timeout=120s
kubectl -n lolday rollout status deploy/frontend --timeout=120s
kubectl -n lolday get deploy backend frontend -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'
```
Expected:
```
backend  harbor.lolday.svc:80/lolday/lolday-backend:phase11e
frontend harbor.lolday.svc:80/lolday/lolday-frontend:phase11e
```

---

## Task 6.3: Smoke verification

- [ ] **Step 1: API smoke — manifest in response**

```bash
curl -s \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  "https://lolday.connlabai.com/api/v1/detectors/$DETECTOR_ID_RFDET/versions/v3.0.0" \
  | jq '.manifest.stages.train.params_schema | keys'
```
Expected: includes `properties`.

- [ ] **Step 2: Playwright e2e smoke**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
source ~/.lolday-cf-svctoken.env
PHASE11E_VERIFY=1 E2E_BASE_URL=https://lolday.connlabai.com pnpm exec playwright test phase11e-full-flow --reporter=line
```
Expected: 2 passed.

- [ ] **Step 3: Submit a small evaluate job (against an existing trained model)**

```bash
# Find a v3.0.0 detector_version_id and an existing model_version + dataset
# Submit:
curl -s -X POST \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"type":"evaluate","detector_version_id":"<uuid>","test_dataset_id":"<uuid>","source_model_version_id":"<uuid>","params":{"threshold":0.5},"resource_profile":"cpu"}' \
  https://lolday.connlabai.com/api/v1/jobs
```
Expected: 202 with the new job's id.

- [ ] **Step 4: Wait stage_end → check summary_metrics**

```bash
sleep 120
JOB_ID=<uuid-from-step-3>
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -tAc \
  "SELECT summary_metrics FROM job WHERE id='$JOB_ID';"
```
Expected: non-null JSON with `metrics` and `confusion_matrix` keys.

- [ ] **Step 5: Visit `/jobs` list and confirm tile**

Either via Playwright run output (Task 6.3 step 2) or manually open browser (user-side).

---

## Task 6.4: Optional backfill of audit-trail jobs

- [ ] **Step 1: Run backfill**

```bash
kubectl -n lolday exec deploy/backend -- uv run python /app/scripts/backfill-summary-metrics.py
```
Expected: log line per terminal job with null summary_metrics; clean exit.

- [ ] **Step 2: Spot-check one of the historical jobs**

```bash
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -tAc \
  "SELECT id, summary_metrics FROM job WHERE id='b4430357-00a8-439c-881e-a45f470363ee';"
```
Expected: `summary_metrics` populated with the CNN train job's final metrics.

---

## Task 6.5: Update Phase 11 progress memory

**Files:**
- Modify: `/home/bolin8017/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_phase11_progress.md`

- [ ] **Step 1: Add phase 11e section**

Document:
- maldet 1.1.0 release commit + PyPI version
- elfrfdet 3.0.0 + elfcnndet 3.0.0 commit / image digests
- Lolday backend phase11e + frontend phase11e digests + helm rev
- Confirm chart, summary card, list-tile all working
- Mark deferred items (primary_metric, list-page filtering) as phase 11f+

---

# Self-Review Checklist

After completing all batches:

- [ ] All `pytest` (maldet, lolday backend) suites green at HEAD on each repo.
- [ ] `pnpm test` + `pnpm typecheck` green in lolday frontend.
- [ ] `phase11e-full-flow` Playwright spec passes against deployed cluster (opt-in).
- [ ] DB rows for v3.0.0 detector_version contain non-null `manifest.stages.train.params_schema`.
- [ ] Submitting an evaluate job populates `summary_metrics` after stage_end.
- [ ] `/jobs` list page tile renders for terminal jobs.
- [ ] `/jobs/:id` Live metrics chart hides for evaluate/predict jobs (no time series).
- [ ] `/jobs/:id` Summary card renders with metric tiles for the just-submitted evaluate job.
- [ ] `JsonViewer` on detector page shows full manifest including the schema.
- [ ] No `parseParams` references remain in frontend.
- [ ] No `services/jobs_params_guard.py` reference remains in backend.
- [ ] Phase 11 progress memory updated.
