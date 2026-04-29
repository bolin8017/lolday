# Phase 11c — Detector contract migration to v2 + template detectors v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the lolday platform's detector contract from v0 (`BaseDetector` ABC + per-detector Pydantic config schema) to v2 (`maldet.toml` + OCI manifest label) end-to-end — across both validators, the build pipeline, the data model, and the job-submission API — and rewrite the two reference detector repos (`elfrfdet`, `elfcnndet`) as v2.0.0 on top of the now-fixed pipeline.

**Architecture:** All v0 codepaths are **deleted**, not deprecated. There is no compat shim. After this phase, the platform talks v2 only. The detector contract is now: (1) `maldet.toml` at repo root, (2) Dockerfile with the standard 5 ARGs / 6 LABELs from `maldet scaffold`, (3) `maldet check` passes locally. Build-time, the build-helper init container runs `maldet check` and writes the 5 build-args to a shared `EmptyDir`; the `buildkit` container reads them and emits `--opt build-arg:KEY=VAL`. Reconcile-time, the manifest is sourced from the Harbor OCI label and persisted in `detector_version.manifest`. The user-supplied `body.params` is no longer schema-validated; instead, a small allowlist guard rejects Hydra-meta fields (`_target_`, `_partial_`, `_args_`, `_recursive_`) and platform-controlled prefixes (`paths.`, `data.`, `mlflow.`).

**Tech stack:** maldet 1.0 (PyPI), Pydantic v2 (`DetectorManifest`), BuildKit `--opt build-arg`, Alembic, FastAPI, scikit-learn 1.4 (rf), Lightning ≥ 2.5 + DDP (cnn), pyelftools 0.31, GitHub Actions for the detector-repo CI.

---

## Pre-flight context

**State of the repo at plan start:**

- `lolday/main` is at `5e2b176` (Phase 11b merged 2026-04-24).
- `maldet 1.0.0` is on PyPI; `maldet scaffold --template rf|cnn` is operational.
- The two detector repos (`bolin8017/elfrfdet`, `bolin8017/elfcnndet`) are still on v0.x tags (`v0.1.1`, `v0.2.1`).

**Concrete v0 surfaces being deleted in this phase:**

| File / surface                                                                                                                                                              | Action                                                                             |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `backend/app/services/validator.py::_check_base_detector_import`                                                                                                            | delete; replaced by `_check_maldet_toml`                                           |
| `backend/app/routers/internal.py::submit_schema` (`POST /internal/builds/{build_id}/schema`)                                                                                | delete; nothing POSTs schema in v2                                                 |
| `backend/app/models/detector.py::DetectorBuild.pending_schema`                                                                                                              | drop column                                                                        |
| `backend/app/models/detector.py::DetectorVersion.config_schema`                                                                                                             | drop column                                                                        |
| `backend/app/schemas/detector.py::DetectorVersionRead.config_schema`                                                                                                        | drop field                                                                         |
| `backend/app/reconciler.py::_handle_succeeded` line `config_schema=b.pending_schema or {}`                                                                                  | drop kwarg                                                                         |
| `backend/app/routers/jobs.py::jsonschema.validate(body.params, dv.config_schema)`                                                                                           | delete; replaced by `validate_user_params(body.params)`                            |
| `charts/lolday/helpers/build-helper/maldet_validator.py::_discover_via_ast` + AST `BaseDetector` walk + `_install_lightweight_deps` + `_load_config_class` + `_post_schema` | delete; replaced by `validate_manifest` (manifest-driven) + `write_build_args`     |
| `charts/lolday/helpers/build-helper/test_maldet_validator.py`                                                                                                               | rewrite for the new validator                                                      |
| `charts/lolday/helpers/build-helper/Dockerfile`                                                                                                                             | install `maldet[lightning] >= 1.0` so the validator can import the manifest module |
| `backend/app/config.py::BUILD_IMAGE_HELPER = "...:v2"`                                                                                                                      | bump to `:v3`                                                                      |
| `charts/lolday/Chart.yaml::version`                                                                                                                                         | `0.13.0` → `0.14.0`; `appVersion: phase11b` → `phase11c`                           |
| Test fixtures across `backend/tests/*` passing `config_schema={...}`                                                                                                        | drop the kwarg                                                                     |

**v2 surfaces being introduced:**

| File / surface                                                                   | Purpose                                                                                                                    |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `backend/app/services/validator.py::_check_maldet_toml(repo_root)`               | parses `maldet.toml` via `maldet.manifest.load_manifest`; raises `StaticValidationError("manifest_invalid", …)` on failure |
| `backend/app/services/jobs_params_guard.py` (new) `validate_user_params(params)` | rejects Hydra meta + platform-controlled prefixes                                                                          |
| `backend/app/services/build.py` `prep-buildargs` initContainer                   | reads `/workspace/src/maldet.toml`, writes `/workspace/build-args/*.env`                                                   |
| `backend/app/services/build.py` buildkit container args                          | reads `/workspace/build-args/*.env` and emits `--opt build-arg:KEY=VAL` flags                                              |
| `charts/lolday/helpers/build-helper/maldet_validator.py::main()` (new shape)     | `maldet check` + computes 5 build-args + writes them to `/workspace/build-args/*.env`                                      |
| New Alembic migration `phase_11c_drop_v0_schema_columns.py`                      | drops `detector_build.pending_schema` + `detector_version.config_schema`                                                   |

**Branching:**

- Lolday work: branch `phase-11c-impl` off `main`. Use `git worktree` from `~/Documents/repositories/lolday/` if isolation desired.
- Detector repos: branch `phase-11c-v2-rewrite` off each repo's `main`.

---

## File Structure

### `lolday` (this repo) — created/modified

| Path                                                                    | Change                                                                                                   |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `backend/app/services/validator.py`                                     | drop `_check_base_detector_import`; add `_check_maldet_toml`; `validate_repo_static` calls the new check |
| `backend/app/services/jobs_params_guard.py`                             | new — `validate_user_params(params)`                                                                     |
| `backend/app/services/build.py`                                         | add `prep-buildargs` initContainer + buildkit `--opt build-arg` flags                                    |
| `backend/app/routers/internal.py`                                       | delete `submit_schema` route                                                                             |
| `backend/app/routers/detectors.py`                                      | unchanged caller of `validate_repo_static` (now manifest-driven via the rewritten function)              |
| `backend/app/routers/jobs.py`                                           | drop `jsonschema.validate`; call `validate_user_params`                                                  |
| `backend/app/reconciler.py`                                             | drop `config_schema=b.pending_schema or {}` from `DetectorVersion(...)` constructor                      |
| `backend/app/models/detector.py`                                        | drop `DetectorBuild.pending_schema`, `DetectorVersion.config_schema`                                     |
| `backend/app/schemas/detector.py`                                       | drop `config_schema` field from `DetectorVersionRead` (or whatever schema includes it)                   |
| `backend/app/config.py`                                                 | `BUILD_IMAGE_HELPER` `:v2` → `:v3`                                                                       |
| `backend/migrations/versions/<rev>_phase_11c_drop_v0_schema_columns.py` | new                                                                                                      |
| `backend/tests/conftest.py`                                             | drop `config_schema=…` kwargs from any `DetectorVersion(...)` factories                                  |
| `backend/tests/test_*.py`                                               | drop `config_schema=…` from fixtures (8 occurrences identified at plan time)                             |
| `backend/tests/test_services_validator.py`                              | new tests for `_check_maldet_toml`                                                                       |
| `backend/tests/test_services_jobs_params_guard.py`                      | new tests for `validate_user_params`                                                                     |
| `backend/tests/test_services_build_args.py`                             | new tests for prep-buildargs + buildkit args                                                             |
| `backend/tests/test_internal_routes.py`                                 | drop tests for the deleted schema route                                                                  |
| `charts/lolday/helpers/build-helper/maldet_validator.py`                | full rewrite                                                                                             |
| `charts/lolday/helpers/build-helper/test_maldet_validator.py`           | full rewrite                                                                                             |
| `charts/lolday/helpers/build-helper/pyproject.toml`                     | new — declares `maldet[lightning] >= 1.0` (dev: pytest)                                                  |
| `charts/lolday/helpers/build-helper/Dockerfile`                         | install `maldet[lightning] >= 1.0`                                                                       |
| `charts/lolday/Chart.yaml`                                              | version `0.13.0` → `0.14.0`; `appVersion: phase11b` → `phase11c`                                         |
| `charts/lolday/values.yaml`                                             | bump build-helper image to `:v3` (if templated)                                                          |
| `docs/superpowers/plans/2026-04-26-phase11c-template-detectors-v2.md`   | this file                                                                                                |

### `bolin8017/elfrfdet` (full overwrite as v2.0.0)

| Path                       | Source                                          | Notes                                                                                                  |
| -------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `maldet.toml`              | `maldet scaffold rf`                            | bump `[detector].version` to `2.0.0`; expand description                                               |
| `Dockerfile`               | scaffold (verbatim)                             | already correct                                                                                        |
| `pyproject.toml`           | scaffold + specialization                       | `version=2.0.0`, description, license, authors, readme, classifiers, urls, dev extras, ruff, pytest    |
| `README.md`                | new content                                     | project doc                                                                                            |
| `CHANGELOG.md`             | new                                             | v2.0.0 breaking change                                                                                 |
| `LICENSE`                  | preserve                                        | MIT, bolin8017                                                                                         |
| `.gitignore`               | new                                             | Python defaults                                                                                        |
| `src/elfrfdet/__init__.py` | scaffold (empty body) + `__version__ = "2.0.0"` | **no `import maldet` shim** — the new validator reads `maldet.toml`, not AST-scans for `import maldet` |
| `src/elfrfdet/features.py` | scaffold (verbatim)                             | Text256Extractor with pyelftools try/except                                                            |
| `src/elfrfdet/models.py`   | scaffold (verbatim)                             | `make_rf`                                                                                              |
| `tests/__init__.py`        | new                                             | empty                                                                                                  |
| `tests/test_features.py`   | new                                             | system ELF + truncated ELF + no-`.text`                                                                |
| `tests/test_manifest.py`   | new                                             | `maldet.manifest.load_manifest` shape checks                                                           |
| `.github/workflows/ci.yml` | new                                             | py3.12 → install → `maldet check` → pytest + ruff                                                      |

### `bolin8017/elfcnndet` (full overwrite as v2.0.0)

Same shape, with `--template cnn` differences (lightning, supports_distributed=ddp, multi-GPU manifest) and an extra `tests/test_models.py` smoke-checking `make_cnn() : LightningModule`.

---

## Execution structure for subagent-driven-development

Three independent workstreams that can run in parallel:

- **Stream L (lolday backend + build-helper + chart):** Parts B–I. Sequential within the stream. Heavy — ~25 sub-tasks, ~3 hours.
- **Stream R (elfrfdet v2.0.0):** Part R. Sequential. ~30 minutes.
- **Stream C (elfcnndet v2.0.0):** Part C2. Sequential. ~30 minutes.

After all three streams' PRs are merged + tags pushed, **operator** runs **Part J** (E2E via the now-fixed pipeline) and updates memory in **Part Z**.

When dispatching subagents:

- Use `superpowers:dispatching-parallel-agents` to launch L, R, C simultaneously.
- Two-stage review per task: (1) compile/lint/type-check; (2) design review against this plan.
- The detector streams (R, C2) have no dependency on Stream L being merged — they only need the detector repos cloned. Stream L's PR also has no dependency on the detector PRs.
- Part J is operator-driven — agents stop at PR open and operator handles merges + the E2E run.

---

## Part A — Workspace setup

### Task A-1: Verify maldet 1.0 is installed locally

**Files:** none.

- [ ] **Step 1: Verify**

```bash
maldet --version 2>/dev/null || pip install --user 'maldet[lightning,mlflow]==1.0.*'
python -c "import maldet, sys; v=maldet.__version__; print(v); sys.exit(0 if v.startswith('1.') else 1)"
```

- [ ] **Step 2: Smoke-test scaffold**

```bash
maldet scaffold --template rf --name _smoketest --out /tmp/_smoketest
test -f /tmp/_smoketest/maldet.toml && rm -rf /tmp/_smoketest
```

### Task A-2: Clone the detector repos

**Files:** `~/Documents/repositories/elfrfdet/`, `~/Documents/repositories/elfcnndet/` (full clones).

- [ ] **Step 1: Clone elfrfdet**

```bash
cd ~/Documents/repositories
test -d elfrfdet && rm -rf elfrfdet
git clone https://github.com/bolin8017/elfrfdet.git
cd elfrfdet && git checkout -b phase-11c-v2-rewrite
git tag --list  # expect v0.1.1
```

- [ ] **Step 2: Clone elfcnndet**

```bash
cd ~/Documents/repositories
test -d elfcnndet && rm -rf elfcnndet
git clone https://github.com/bolin8017/elfcnndet.git
cd elfcnndet && git checkout -b phase-11c-v2-rewrite
git tag --list  # expect v0.2.1
```

### Task A-3: lolday phase-11c-impl branch

**Files:** none.

- [ ] **Step 1: Branch off main**

```bash
cd ~/Documents/repositories/lolday
git checkout main
git pull --ff-only
git checkout -b phase-11c-impl
```

(Optional: `git worktree add ../lolday-phase11c phase-11c-impl` for isolation; the plan assumes the inline branch path.)

---

## Part B — Backend `validate_repo_static` rewrite (Stream L)

**Pre-read:**

- `backend/app/services/validator.py` — current implementation, lines 17–85.
- `backend/app/routers/detectors.py:60,126` — only caller, passes a tmpdir of the cloned repo.
- `~/Documents/repositories/maldet/src/maldet/manifest.py` — `load_manifest`, `DetectorManifest`, `ManifestNotFoundError`.

### Task B-1: Replace `_check_base_detector_import` with `_check_maldet_toml`

**Files:**

- Modify: `backend/app/services/validator.py`
- Test: `backend/tests/test_services_validator.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_services_validator.py`:

```python
"""Tests for validate_repo_static — manifest-driven (Phase 11c)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.services.validator import StaticValidationError, validate_repo_static


def _write_minimal_v2_repo(repo: Path, *, framework: str = "sklearn") -> None:
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (repo / "maldet.toml").write_text(textwrap.dedent(f"""
        [detector]
        name = "demo"
        version = "1.0.0"
        framework = "{framework}"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = {{ path = "model/", type = "dir" }}
    """).strip() + "\n")


def test_v2_repo_with_valid_maldet_toml_passes(tmp_path: Path) -> None:
    _write_minimal_v2_repo(tmp_path)
    validate_repo_static(tmp_path)  # must not raise


def test_missing_maldet_toml_raises_manifest_missing(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_missing"


def test_invalid_manifest_raises_manifest_invalid(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    # Missing required [output] section.
    (tmp_path / "maldet.toml").write_text('[detector]\nname = "x"\nversion = "1"\nframework = "sklearn"\n')
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_invalid"


def test_v0_repo_without_maldet_toml_no_longer_passes(tmp_path: Path) -> None:
    """Phase 11c removes the BaseDetector AST escape hatch — v0 detectors must fail."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("from maldet import BaseDetector\nclass D(BaseDetector): pass\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_missing"
```

- [ ] **Step 2: Run failing**

```bash
cd backend
uv run pytest tests/test_services_validator.py -v
```

Expected: 3 of 4 fail (the existing implementation rejects the v0-style test on the `BaseDetector` AST scan but doesn't have `manifest_missing` or `manifest_invalid` codes).

- [ ] **Step 3: Rewrite `validator.py`**

Replace the body of `backend/app/services/validator.py` (the existing `_check_base_detector_import` and the `validate_repo_static` orchestration) with:

```python
import tomllib
from pathlib import Path

import pydantic
from maldet.manifest import DetectorManifest

from app.config import settings

REPO_MAX_SIZE_BYTES = settings.REPO_MAX_SIZE_MB * 1024 * 1024


class StaticValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_repo_static(repo_root: Path) -> None:
    """Raise StaticValidationError on failure; return silently on success."""
    _check_size(repo_root)
    _check_pyproject(repo_root)
    _check_dockerfile(repo_root)
    _check_maldet_toml(repo_root)


def _check_size(repo_root: Path) -> None:
    total = 0
    for p in repo_root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            if total > REPO_MAX_SIZE_BYTES:
                raise StaticValidationError(
                    "repo_too_large",
                    f"repo exceeds {REPO_MAX_SIZE_BYTES} bytes",
                )


def _check_pyproject(repo_root: Path) -> None:
    pp = repo_root / "pyproject.toml"
    if not pp.is_file():
        raise StaticValidationError("pyproject_missing", "pyproject.toml not found")
    try:
        content = pp.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise StaticValidationError(
            "pyproject_unparseable", f"pyproject.toml is not valid UTF-8: {e}"
        ) from e
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        raise StaticValidationError(
            "pyproject_unparseable", f"pyproject.toml is not valid TOML: {e}"
        ) from e


def _check_dockerfile(repo_root: Path) -> None:
    if not (repo_root / "Dockerfile").is_file():
        raise StaticValidationError(
            "dockerfile_missing", "Dockerfile required at repo root"
        )


def _check_maldet_toml(repo_root: Path) -> None:
    """Phase 11c: the only "is this a detector repo?" signal is a parseable
    `maldet.toml` that satisfies the ``DetectorManifest`` schema."""
    manifest_path = repo_root / "maldet.toml"
    if not manifest_path.is_file():
        raise StaticValidationError(
            "manifest_missing",
            "maldet.toml required at repo root (Phase 11c contract)",
        )
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise StaticValidationError(
            "manifest_unparseable",
            f"maldet.toml is not valid TOML: {e}",
        ) from e
    try:
        DetectorManifest.model_validate(data)
    except pydantic.ValidationError as e:
        raise StaticValidationError(
            "manifest_invalid",
            f"maldet.toml fails DetectorManifest schema: {e}",
        ) from e


# ---------------------------------------------------------------------------
# Phase 11b: job-submission pre-flight validators (unchanged in 11c)
# ---------------------------------------------------------------------------

from app.models.job import ResourceProfile  # noqa: E402

_PROFILE_TO_MANIFEST_TOKEN = {
    ResourceProfile.STANDARD: "cpu",
    ResourceProfile.GPU2: "gpu2",
}

SUPPORTED_DATASET_CONTRACTS = frozenset({"sample_csv"})


class JobSubmissionError(ValueError):
    """Raised when a job cannot be accepted given the detector's manifest."""


def validate_job_submission(
    *,
    manifest: DetectorManifest,
    resource_profile: ResourceProfile,
    dataset_contract: str,
    stage: str,
) -> None:
    # body unchanged from Phase 11b — keep as-is
    token = _PROFILE_TO_MANIFEST_TOKEN.get(resource_profile)
    if token is None or token not in manifest.resources.supports:
        raise JobSubmissionError(
            f"resource_profile {resource_profile.value!r} (manifest token {token!r}) "
            f"not in detector.resources.supports={manifest.resources.supports}"
        )
    if dataset_contract != manifest.input.dataset_contract:
        raise JobSubmissionError(
            f"dataset_contract mismatch: platform sent {dataset_contract!r}, "
            f"detector expects {manifest.input.dataset_contract!r}"
        )
    if dataset_contract not in SUPPORTED_DATASET_CONTRACTS:
        raise JobSubmissionError(
            f"dataset_contract {dataset_contract!r} not supported by the platform; "
            f"supported: {sorted(SUPPORTED_DATASET_CONTRACTS)}"
        )
    if stage not in manifest.lifecycle.stages:
        raise JobSubmissionError(
            f"stage {stage!r} not declared in detector.lifecycle.stages={manifest.lifecycle.stages}"
        )
    if resource_profile == ResourceProfile.GPU2 and not manifest.lifecycle.supports_distributed:
        raise JobSubmissionError(
            f"resource_profile {resource_profile.value!r} allocates multiple GPUs but "
            f"detector's lifecycle.supports_distributed is "
            f"{manifest.lifecycle.supports_distributed!r}; set supports_distributed to "
            f"ddp/fsdp/deepspeed to accept multi-GPU jobs"
        )
```

(`validate_job_submission` is preserved verbatim; `import ast` is removed.)

- [ ] **Step 4: Run — pass**

```bash
uv run pytest tests/test_services_validator.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/validator.py backend/tests/test_services_validator.py
git commit -m "feat(validator): phase 11c — replace BaseDetector AST scan with maldet.toml parse"
```

---

## Part C — Drop schema POST route + `pending_schema` column

### Task C-1: Delete `submit_schema` and its tests

**Files:**

- Modify: `backend/app/routers/internal.py`
- Modify: `backend/tests/test_internal_routes.py` (search for `submit_schema` / `/builds/.*/schema` tests)

- [ ] **Step 1: Locate tests of the schema route**

```bash
grep -rn 'submit_schema\|/builds/.*schema\|"/schema"' backend/tests --include="*.py"
```

- [ ] **Step 2: Delete the route in `routers/internal.py`**

Remove the `@router.post("/builds/{build_id}/schema")` decorator + `submit_schema` function (lines ~22–35).

Also remove the unused import:

```python
from app.deps import require_build_token  # check whether this is still used after the deletion
```

If `require_build_token` becomes unreferenced after the delete, also drop its import (and check for orphans elsewhere — `grep -rn require_build_token backend/app`).

- [ ] **Step 3: Delete tests of the schema route**

Delete the entire test functions that exercise the schema endpoint. If a whole test file becomes empty, `git rm` it.

- [ ] **Step 4: Run remaining tests**

```bash
uv run pytest backend/tests/test_internal_routes.py -v
```

Expected: tests pass. If any test still references `submit_schema` or `/builds/.../schema`, keep deleting.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/internal.py backend/tests/
git commit -m "refactor: phase 11c — drop /internal/builds/{id}/schema route (v0 carryover)"
```

### Task C-2: Drop `DetectorBuild.pending_schema` field

**Files:**

- Modify: `backend/app/models/detector.py`
- Modify: `backend/app/schemas/detector.py`
- Modify: `backend/app/reconciler.py` (already covered in F-2; defer)

- [ ] **Step 1: Drop the ORM field**

In `backend/app/models/detector.py`, remove line:

```python
pending_schema: Mapped[dict | None] = mapped_column(_JSONB)
```

- [ ] **Step 2: Drop the schema field**

In `backend/app/schemas/detector.py`, remove the corresponding `pending_schema` field if present (the line `"pending_schema"` reference indicated only a comment; verify the schema's emitted shape).

- [ ] **Step 3: Run unit tests for models**

```bash
cd backend
uv run pytest tests/test_models_detector.py -v
```

Expect: a failure pointing at the `config_schema` field on `DetectorVersion` (this is dropped in F-2). Add a regression assertion that `pending_schema` is gone:

In `backend/tests/test_models_detector.py`, locate the existing `test_detector_build_columns` (or similar) and ADD an assertion `assert "pending_schema" not in DetectorBuild.__table__.columns`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/detector.py backend/app/schemas/detector.py backend/tests/test_models_detector.py
git commit -m "refactor: phase 11c — drop DetectorBuild.pending_schema (v0 schema column)"
```

The Alembic migration that actually drops the database column lives in **Task F-1**.

---

## Part D — Build-helper `maldet_validator` rewrite (Stream L)

**Pre-read:**

- `charts/lolday/helpers/build-helper/maldet_validator.py` — current AST-based shape.
- `charts/lolday/helpers/build-helper/Dockerfile` — `python:3.12-slim` + `git`/`uv`/`httpx`.
- `backend/app/services/build.py:197–245` — the `validate` initContainer that runs this script.

### Task D-1: Rewrite `maldet_validator.py` as manifest-driven

**Files:**

- Modify: `charts/lolday/helpers/build-helper/maldet_validator.py` (full rewrite)
- Modify: `charts/lolday/helpers/build-helper/test_maldet_validator.py` (full rewrite)
- Create: `charts/lolday/helpers/build-helper/pyproject.toml` (new — declarative deps)
- Modify: `charts/lolday/helpers/build-helper/Dockerfile` (install maldet)

- [ ] **Step 1: Write failing tests (full rewrite)**

Overwrite `charts/lolday/helpers/build-helper/test_maldet_validator.py`:

```python
"""Phase 11c manifest-driven validator tests."""

from __future__ import annotations

import base64
import json
import sys
import textwrap
from pathlib import Path

import pytest

# Tests live next to the validator script; import the module directly.
sys.path.insert(0, str(Path(__file__).parent))
import maldet_validator as mv  # noqa: E402


def _write_repo(root: Path, *, framework: str = "sklearn", name: str = "demo") -> None:
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\nrequires-python = ">=3.12"\n'
    )
    (root / "maldet.toml").write_text(textwrap.dedent(f"""
        [detector]
        name = "{name}"
        version = "2.0.0"
        framework = "{framework}"

        [input]
        binary_format = "elf"
        dataset_contract = "sample_csv"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = {{ path = "model/", type = "dir" }}
    """).strip() + "\n")
    src = root / "src" / name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")


def test_validate_manifest_returns_parsed_manifest(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    m = mv.validate_manifest(tmp_path)
    assert m.detector.name == "demo"
    assert m.detector.framework == "sklearn"


def test_validate_manifest_raises_when_missing(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM x\n")
    with pytest.raises(mv.ValidationError, match="manifest_missing"):
        mv.validate_manifest(tmp_path)


def test_validate_manifest_raises_on_invalid_schema(tmp_path: Path) -> None:
    (tmp_path / "maldet.toml").write_text('[detector]\nname = "x"\n')  # missing many required fields
    with pytest.raises(mv.ValidationError, match="manifest_invalid"):
        mv.validate_manifest(tmp_path)


def test_write_build_args_emits_five_files(tmp_path: Path) -> None:
    _write_repo(tmp_path / "src", name="demo", framework="lightning")
    out = tmp_path / "build-args"
    out.mkdir()
    git_sha_path = tmp_path / "git-sha"
    git_sha_path.write_text("abc123def\n")

    mv.write_build_args(repo=tmp_path / "src", out=out, git_sha_path=git_sha_path)

    expected = {
        "MALDET_NAME": "demo",
        "MALDET_VERSION": "2.0.0",
        "MALDET_FRAMEWORK": "lightning",
        "GIT_COMMIT": "abc123def",
    }
    for key, val in expected.items():
        assert (out / f"{key}").read_text() == val
    # MANIFEST_B64 is a non-empty base64 of the JSON-serialized manifest
    b64 = (out / "MALDET_MANIFEST_B64").read_text()
    assert b64
    decoded = json.loads(base64.b64decode(b64))
    assert decoded["detector"]["name"] == "demo"


def test_write_build_args_missing_git_sha_uses_empty_string(tmp_path: Path) -> None:
    _write_repo(tmp_path / "src", name="demo")
    out = tmp_path / "build-args"
    out.mkdir()
    mv.write_build_args(repo=tmp_path / "src", out=out, git_sha_path=tmp_path / "absent")
    assert (out / "GIT_COMMIT").read_text() == ""


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through main(): valid repo + write-out → exit 0."""
    _write_repo(tmp_path)
    out = tmp_path / "build-args"
    out.mkdir()
    (tmp_path / "git-sha").write_text("deadbeef\n")
    monkeypatch.setattr(sys, "argv", ["maldet_validator", str(tmp_path), str(out)])
    rc = mv.main()
    assert rc == 0
    assert (out / "MALDET_NAME").read_text() == "demo"
```

- [ ] **Step 2: Rewrite `maldet_validator.py`**

Overwrite the file (drop everything from the old AST/install/post path):

```python
"""Phase 11c manifest-driven validator for the lolday build pipeline.

Runs inside the ``validate`` init container of a detector build Job. It:

1. Parses ``maldet.toml`` via ``maldet.manifest.load_manifest`` (Pydantic
   ``DetectorManifest``). Fail-fast on missing or schema-invalid manifests.
2. Computes the five build-args and writes them to ``/workspace/build-args/``
   so the ``buildkit`` container can convert them to ``--opt build-arg``
   flags. The args are split per-file (one ENV-style file per arg) to avoid
   shell-quoting bugs around the long base64 manifest string.

There is no per-file shell parsing or AST scanning. The validator does not
install the detector repo; ``maldet[lightning] >= 1.0`` is preinstalled in
the build-helper image so the manifest module imports cleanly.

The validator does NOT run ``maldet check`` here, because that requires
``pip install`` of the detector repo (and pulls torch / sklearn). The
``buildkit`` container will fail loudly if any entrypoint dotted-path is
unreachable at container-startup time, surfacing the same class of error
without the install cost.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from maldet.manifest import DetectorManifest, ManifestNotFoundError, load_manifest

# Files written under build-args/.
ARG_NAMES = ("MALDET_NAME", "MALDET_VERSION", "MALDET_FRAMEWORK", "MALDET_MANIFEST_B64", "GIT_COMMIT")


class ValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_manifest(repo: Path) -> DetectorManifest:
    """Return the parsed DetectorManifest, or raise ValidationError."""
    manifest_path = repo / "maldet.toml"
    if not manifest_path.is_file():
        raise ValidationError(
            "manifest_missing",
            f"maldet.toml not found at {manifest_path} (Phase 11c contract)",
        )
    try:
        return load_manifest(manifest_path)
    except ManifestNotFoundError as exc:
        raise ValidationError("manifest_missing", str(exc)) from exc
    except Exception as exc:
        # Pydantic ValidationError, TOMLDecodeError, etc.
        raise ValidationError("manifest_invalid", f"{type(exc).__name__}: {exc}") from exc


def write_build_args(*, repo: Path, out: Path, git_sha_path: Path) -> None:
    """Compute the 5 build-args and write each to ``out/<NAME>``."""
    manifest = validate_manifest(repo)
    git_sha = git_sha_path.read_text().strip() if git_sha_path.is_file() else ""
    manifest_b64 = base64.b64encode(
        json.dumps(manifest.model_dump(mode="json"), separators=(",", ":"), default=str).encode("utf-8")
    ).decode("ascii")
    values = {
        "MALDET_NAME": manifest.detector.name,
        "MALDET_VERSION": manifest.detector.version,
        "MALDET_FRAMEWORK": manifest.detector.framework,
        "MALDET_MANIFEST_B64": manifest_b64,
        "GIT_COMMIT": git_sha,
    }
    for name in ARG_NAMES:
        (out / name).write_text(values[name])


def main() -> int:
    """``maldet_validator <repo_path> [<build_args_out>]``.

    The init-container invocation passes both paths; tests pass them too.
    """
    if len(sys.argv) < 2:
        return _fail("usage", "maldet_validator <repo_path> [<build_args_out>]")
    repo = Path(sys.argv[1])
    if not repo.is_dir():
        return _fail("repo_missing", f"not a directory: {repo}")

    out = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("/workspace/build-args")
    out.mkdir(parents=True, exist_ok=True)

    # The clone init container writes git-sha to /workspace/git-sha.
    git_sha_path = repo.parent / "git-sha"

    try:
        manifest = validate_manifest(repo)
        write_build_args(repo=repo, out=out, git_sha_path=git_sha_path)
        print(
            f"VALIDATION OK: name={manifest.detector.name} "
            f"version={manifest.detector.version} framework={manifest.detector.framework}",
            flush=True,
        )
        return 0
    except ValidationError as e:
        return _fail(e.code, e.message)
    except Exception as e:
        return _fail("validation_error", f"{type(e).__name__}: {e}")


def _fail(code: str, message: str) -> int:
    payload = {"validation_error": {"code": code, "message": message}}
    print(json.dumps(payload), flush=True, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Add `pyproject.toml` so tests can run with maldet installed**

Create `charts/lolday/helpers/build-helper/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "lolday-build-helper"
version = "3.0.0"
description = "lolday build-pipeline init-container script (Phase 11c manifest-driven)"
requires-python = ">=3.12"
dependencies = [
    "maldet[lightning]>=1.0,<2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.hatch.build.targets.wheel]
# Single-file module; bundle it directly.
include = ["maldet_validator.py"]
```

- [ ] **Step 4: Run tests**

```bash
cd charts/lolday/helpers/build-helper
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
pytest test_maldet_validator.py -v
deactivate
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/repositories/lolday
git add charts/lolday/helpers/build-helper/maldet_validator.py \
        charts/lolday/helpers/build-helper/test_maldet_validator.py \
        charts/lolday/helpers/build-helper/pyproject.toml
git commit -m "feat(build-helper): phase 11c — manifest-driven validator + build-args writer"
```

### Task D-2: Update build-helper Dockerfile

**Files:**

- Modify: `charts/lolday/helpers/build-helper/Dockerfile`

- [ ] **Step 1: Replace Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'maldet[lightning]>=1.0,<2.0'

WORKDIR /app
COPY maldet_validator.py .

USER 1000
ENTRYPOINT ["python", "-m"]
CMD ["maldet_validator"]
```

(The old image installed `git` + `uv` + `httpx`. We no longer clone the repo (clone runs in a separate init container) or POST anything (HTTP client gone). `git` is removed too; the clone init container's image already has it.)

- [ ] **Step 2: Smoke-test the Dockerfile builds**

```bash
cd charts/lolday/helpers/build-helper
docker build -t lolday-build-helper:phase11c-test .
docker run --rm lolday-build-helper:phase11c-test maldet_validator 2>&1 | head -3
```

Expected: prints the `usage` validation error (because no repo path supplied). Image builds clean.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/helpers/build-helper/Dockerfile
git commit -m "build(build-helper): phase 11c Dockerfile — install maldet, drop git/uv/httpx"
```

---

## Part E — `build.py` build-args injection (Stream L)

**Pre-read:**

- `backend/app/services/build.py::build_job_spec` lines 49–326. The structure is `clone → validate → buildkit`.
- The `validate` initContainer currently runs `python -m maldet_validator /workspace/src`. After D-1 it accepts an optional second arg for the build-args output dir.

### Task E-1: Add `prep-buildargs` shared volume + buildkit args

**Files:**

- Modify: `backend/app/services/build.py`
- Test: `backend/tests/test_services_build_args.py` (new)

Strategy: We don't need a separate `prep-buildargs` initContainer — the existing `validate` container already does the work after D-1. We just need to:

1. Add a `build-args` `EmptyDir` volume.
2. Mount it into the `validate` container at `/workspace/build-args` (writable).
3. Mount it into the `buildkit` container at `/workspace/build-args` (read-only).
4. Wrap the `buildkit` container's entrypoint in a small shell line that reads the per-file args and emits `--opt build-arg:KEY=VAL` flags before invoking `buildctl`.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_services_build_args.py`:

```python
"""Tests that build_job_spec produces a Job that reads & forwards build-args."""

from __future__ import annotations

from uuid import uuid4

from app.services.build import build_job_spec


def _spec() -> dict:
    return build_job_spec(
        build_id=uuid4(),
        detector_name="elfrfdet",
        git_tag="v2.0.0",
        owner_repo="bolin8017/elfrfdet",
    )


def test_build_args_emptydir_volume_present() -> None:
    spec = _spec()
    vols = {v["name"] for v in spec["spec"]["template"]["spec"]["volumes"]}
    assert "build-args" in vols


def test_validate_container_mounts_build_args_writable() -> None:
    spec = _spec()
    init = next(c for c in spec["spec"]["template"]["spec"]["initContainers"] if c["name"] == "validate")
    mount = next(m for m in init["volumeMounts"] if m["name"] == "build-args")
    # Phase 11c contract: validate writes the per-key files here.
    assert mount["mountPath"] == "/workspace/build-args"
    assert not mount.get("readOnly", False)


def test_validate_container_passes_build_args_dir_in_argv() -> None:
    spec = _spec()
    init = next(c for c in spec["spec"]["template"]["spec"]["initContainers"] if c["name"] == "validate")
    # Validator now takes (repo_path, build_args_out).
    assert init["args"] == ["/workspace/src", "/workspace/build-args"]


def test_buildkit_container_mounts_build_args_readonly() -> None:
    spec = _spec()
    bk = next(c for c in spec["spec"]["template"]["spec"]["containers"] if c["name"] == "buildkit")
    mount = next(m for m in bk["volumeMounts"] if m["name"] == "build-args")
    assert mount["mountPath"] == "/workspace/build-args"
    assert mount["readOnly"] is True


def test_buildkit_command_assembles_build_args_from_files() -> None:
    """The buildkit container reads the per-file args and emits --opt build-arg:KEY=VAL.

    A regression here would silently produce an image with empty manifest
    labels (the very bug Phase 11c fixes).
    """
    spec = _spec()
    bk = next(c for c in spec["spec"]["template"]["spec"]["containers"] if c["name"] == "buildkit")
    # We use shell wrapping (sh -c) so the buildkit container can read the
    # files at runtime. Verify the wrapper exists and references each arg.
    cmd_argv = bk["command"] + bk["args"]
    joined = " ".join(cmd_argv)
    for key in ("MALDET_NAME", "MALDET_VERSION", "MALDET_FRAMEWORK", "MALDET_MANIFEST_B64", "GIT_COMMIT"):
        assert key in joined, f"buildkit args do not reference {key}"
    # The five --opt build-arg flags are constructed from the files.
    assert "--opt build-arg:" in joined or "build-arg:MALDET_NAME=" in joined
```

- [ ] **Step 2: Run failing**

```bash
cd backend
uv run pytest tests/test_services_build_args.py -v
```

Expected: 5 fail (no `build-args` volume, validate doesn't get the second argv, buildkit lacks the mount + flags).

- [ ] **Step 3: Implement in `build.py`**

In `backend/app/services/build.py::build_job_spec`, make these edits:

(a) **Add the volume.** Append to the `volumes` list (after `harbor-docker-cfg`):

```python
{
    "name": "build-args",
    "emptyDir": {"sizeLimit": "1Mi"},
},
```

(The five files are tens of KB at most.)

(b) **Update the `validate` initContainer's `args` and add a writable mount:**

```python
"args": ["/workspace/src", "/workspace/build-args"],
```

```python
"volumeMounts": [
    {"name": "workspace", "mountPath": "/workspace"},
    {"name": "tmp", "mountPath": "/tmp"},
    {"name": "build-args", "mountPath": "/workspace/build-args"},
],
```

(c) **Replace the `buildkit` container's `command` and `args` with a shell wrapper.** Find the existing block:

```python
"command": ["buildctl-daemonless.sh"],
"args": [
    "build",
    "--frontend", "dockerfile.v0",
    "--local", "context=/workspace/src",
    "--local", "dockerfile=/workspace/src",
    "--output", f"type=image,name={destination},push=true,registry.insecure=true",
    "--export-cache", f"type=registry,ref={cache_repo},mode=max,registry.insecure=true",
    "--import-cache", f"type=registry,ref={cache_repo},registry.insecure=true",
    "--progress", "plain",
],
```

Replace with:

```python
# We keep buildctl-daemonless.sh as the entry but wrap it in /bin/sh -c so we
# can read the five MALDET_* / GIT_COMMIT files written by the validate
# initContainer and convert them into --opt build-arg flags. The base64
# manifest string can be ~10 KB; passing it through argv avoids shell
# quoting bugs (no `eval`, no `$(...)` substitution).
"command": ["/bin/sh", "-c"],
"args": [
    "set -eu; "
    "BA=/workspace/build-args; "
    "MN=$(cat $BA/MALDET_NAME); "
    "MV=$(cat $BA/MALDET_VERSION); "
    "MF=$(cat $BA/MALDET_FRAMEWORK); "
    "MB=$(cat $BA/MALDET_MANIFEST_B64); "
    "GC=$(cat $BA/GIT_COMMIT); "
    "exec buildctl-daemonless.sh build "
    "--frontend dockerfile.v0 "
    "--local context=/workspace/src "
    "--local dockerfile=/workspace/src "
    f"--output type=image,name={destination},push=true,registry.insecure=true "
    f"--export-cache type=registry,ref={cache_repo},mode=max,registry.insecure=true "
    f"--import-cache type=registry,ref={cache_repo},registry.insecure=true "
    "--progress plain "
    "--opt build-arg:MALDET_NAME=\"$MN\" "
    "--opt build-arg:MALDET_VERSION=\"$MV\" "
    "--opt build-arg:MALDET_FRAMEWORK=\"$MF\" "
    "--opt build-arg:MALDET_MANIFEST_B64=\"$MB\" "
    "--opt build-arg:GIT_COMMIT=\"$GC\""
],
```

(The `f"…"` portions interpolate `destination` and `cache_repo` at Python compile-time; the shell variables `$MN/$MV/$MF/$MB/$GC` are interpolated at runtime by the `sh -c` wrapper. Quoting around the variables is preserved through the shell's normal expansion rules.)

(d) **Add the buildkit container's read-only `build-args` mount.** Append to its `volumeMounts`:

```python
{"name": "build-args", "mountPath": "/workspace/build-args", "readOnly": True},
```

- [ ] **Step 4: Run tests — pass**

```bash
uv run pytest tests/test_services_build_args.py -v
uv run pytest tests/test_services_build.py -v   # ensure no regressions in existing build tests
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/build.py backend/tests/test_services_build_args.py
git commit -m "feat(build): phase 11c — emit MALDET_* build-args from validate to buildkit"
```

---

## Part F — Drop `config_schema` column + jsonschema validation (Stream L)

### Task F-1: Alembic migration to drop both columns

**Files:**

- Create: `backend/migrations/versions/<rev>_phase_11c_drop_v0_schema_columns.py`

- [ ] **Step 1: Generate revision id**

```bash
cd backend
uv run alembic revision -m "phase 11c drop v0 schema columns"
```

This creates a new file under `migrations/versions/<hex>_phase_11c_drop_v0_schema_columns.py`. Note the revision id; the generator wires `down_revision = "74c95d81f74e"` automatically.

- [ ] **Step 2: Implement the migration**

Replace the generated body with:

```python
"""phase 11c drop v0 schema columns

Revision ID: <generated>
Revises: 74c95d81f74e
Create Date: 2026-04-26 ...

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# (alembic generator fills these)
revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "74c95d81f74e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the v0 schema-related columns.

    The pydantic JSON schema flow (validate-init-container → /builds/{id}/schema
    → detector_build.pending_schema → detector_version.config_schema → jobs
    router jsonschema.validate) is replaced in Phase 11c by manifest-driven
    validation. No data preserved on downgrade — compat is not a goal.
    """
    op.drop_column("detector_build", "pending_schema")
    op.drop_column("detector_version", "config_schema")


def downgrade() -> None:
    """Re-add the columns as nullable empty JSON. No row-level data is restored."""
    op.add_column(
        "detector_build",
        sa.Column(
            "pending_schema",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )
    op.add_column(
        "detector_version",
        sa.Column(
            "config_schema",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("detector_version", "config_schema", server_default=None)
```

- [ ] **Step 3: Test migration round-trip on sqlite (CI parity)**

```bash
uv run pytest backend/tests/test_migrations_parity.py -v
```

Expected: pass. (If the test enumerates revisions, it may need a constant updated.)

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/versions/*phase_11c_drop_v0_schema_columns.py
git commit -m "db: phase 11c — drop detector_build.pending_schema + detector_version.config_schema"
```

### Task F-2: Drop ORM `config_schema` field + reconciler write site

**Files:**

- Modify: `backend/app/models/detector.py`
- Modify: `backend/app/schemas/detector.py`
- Modify: `backend/app/reconciler.py`

- [ ] **Step 1: Drop the ORM field**

In `backend/app/models/detector.py`, remove line 88:

```python
config_schema: Mapped[dict] = mapped_column(_JSONB, nullable=False)
```

- [ ] **Step 2: Drop the schema field**

In `backend/app/schemas/detector.py`, remove line 44 (`config_schema: dict[str, Any]`).

- [ ] **Step 3: Drop the reconciler kwarg**

In `backend/app/reconciler.py:354`, replace:

```python
config_schema=b.pending_schema or {},
```

with: (delete the line entirely; the constructor no longer takes that kwarg.)

- [ ] **Step 4: Update test that asserts column shape**

In `backend/tests/test_models_detector.py:17`, the line:

```python
"image_digest", "config_schema", "built_at", "status",
```

becomes:

```python
"image_digest", "built_at", "status",
```

(remove `"config_schema"`).

- [ ] **Step 5: Run unit tests, expect failures from leftover fixtures**

```bash
uv run pytest backend/tests/test_models_detector.py -v
```

These should pass. The remaining fixture failures are addressed in **F-4**.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/detector.py backend/app/schemas/detector.py backend/app/reconciler.py backend/tests/test_models_detector.py
git commit -m "refactor: phase 11c — drop DetectorVersion.config_schema (v0 carryover)"
```

### Task F-3: Drop `jsonschema.validate(body.params, dv.config_schema)` from jobs router

**Files:**

- Modify: `backend/app/routers/jobs.py`

- [ ] **Step 1: Locate and remove**

In `backend/app/routers/jobs.py:145–149`, remove the entire block:

```python
# 4. params schema validation
try:
    jsonschema.validate(instance=body.params, schema=dv.config_schema)
except jsonschema.ValidationError as e:
    raise HTTPException(status_code=422, detail=f"params invalid: {e.message}")
```

If `import jsonschema` becomes unused after this, remove the import. (The Hydra-meta guard is added in Part G; that uses a different module.)

- [ ] **Step 2: Run jobs router tests**

```bash
uv run pytest backend/tests/test_routers_jobs.py -v
```

Expect: tests that submitted invalid params and asserted 422-from-jsonschema will now fail. Update those tests to match the new contract — specifically, tests that submitted body.params with `_target_` should still 422 (the Part G guard catches them).

This is best done together with Part G; mark them xfail temporarily or re-write inline. For commit cleanliness, defer the jsonschema-test rewrites to Part G.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/jobs.py
git commit -m "refactor: phase 11c — drop jsonschema.validate(body.params, dv.config_schema)"
```

### Task F-4: Update test fixtures (`config_schema=…` purge)

**Files:**

- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_services_events_tail.py`
- Modify: `backend/tests/test_reconciler.py`
- Modify: `backend/tests/test_internal_events.py`
- Modify: `backend/tests/test_models_job_event.py`
- Modify: `backend/tests/test_reconciler_events.py`
- Modify: `backend/tests/test_jobs_events_endpoint.py`
- Modify: `backend/tests/test_jobs_events_websocket.py`

- [ ] **Step 1: Purge the kwarg**

```bash
cd backend
grep -rln "config_schema=" tests/ | while read f; do
  python - <<PY
import pathlib, re
p = pathlib.Path("$f")
src = p.read_text()
# Match either "config_schema={...}," or trailing "config_schema={...}\n)"
src = re.sub(r"\s*config_schema=\{[^}]*\},?", "", src)
src = re.sub(r"\s*config_schema=[^,)\n]+,?", "", src)
p.write_text(src)
PY
done
grep -rn "config_schema" tests/ || echo "(none — clean)"
```

Sanity-check: re-grep should return nothing.

- [ ] **Step 2: Run full backend test suite**

```bash
uv run pytest -x
```

Expected: green (or, the only failures are from Part G and Part D's manifest-driven changes that haven't been run-checked yet).

- [ ] **Step 3: Commit**

```bash
git add backend/tests
git commit -m "test: phase 11c — drop config_schema= kwargs from fixtures"
```

---

## Part G — Hydra params guard (Stream L)

### Task G-1: `validate_user_params(params)`

**Files:**

- Create: `backend/app/services/jobs_params_guard.py`
- Test: `backend/tests/test_services_jobs_params_guard.py` (new)

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the Hydra-meta + platform-prefix params guard (Phase 11c)."""

from __future__ import annotations

import pytest

from app.services.jobs_params_guard import (
    UserParamsRejected,
    validate_user_params,
)


def test_simple_overrides_pass() -> None:
    validate_user_params({"model.n_estimators": 500, "trainer.max_epochs": 3})


def test_nested_dict_pass() -> None:
    validate_user_params({"model": {"n_estimators": 500, "max_depth": 8}})


@pytest.mark.parametrize("key", ["_target_", "_partial_", "_args_", "_recursive_"])
def test_hydra_meta_rejected_at_top_level(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({key: "evil.module.func"})


@pytest.mark.parametrize("key", ["_target_", "_partial_", "_args_", "_recursive_"])
def test_hydra_meta_rejected_when_nested(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({"model": {key: "evil.module.func"}})


def test_dotted_hydra_meta_rejected() -> None:
    with pytest.raises(UserParamsRejected, match="_target_"):
        validate_user_params({"model._target_": "evil.module.func"})


@pytest.mark.parametrize("prefix", ["paths", "data", "mlflow"])
def test_platform_controlled_prefix_rejected(prefix: str) -> None:
    with pytest.raises(UserParamsRejected, match=prefix):
        validate_user_params({f"{prefix}.output_dir": "/anywhere"})


@pytest.mark.parametrize("prefix", ["paths", "data", "mlflow"])
def test_platform_controlled_prefix_dict_rejected(prefix: str) -> None:
    with pytest.raises(UserParamsRejected, match=prefix):
        validate_user_params({prefix: {"output_dir": "/anywhere"}})


def test_empty_params_pass() -> None:
    validate_user_params({})


def test_non_dict_rejected() -> None:
    with pytest.raises(UserParamsRejected, match="must be a dict"):
        validate_user_params(["not", "a", "dict"])  # type: ignore[arg-type]
```

- [ ] **Step 2: Run failing**

```bash
cd backend
uv run pytest tests/test_services_jobs_params_guard.py -v
```

Expected: ImportError (module doesn't exist).

- [ ] **Step 3: Implement**

Create `backend/app/services/jobs_params_guard.py`:

```python
"""Guard for user-supplied Hydra overrides on job submission.

Phase 11b/11c removed the v0 per-detector pydantic JSON schema validation. To
keep the platform from accepting Hydra overrides that would (a) execute
arbitrary code via ``_target_`` instantiation or (b) clobber platform-controlled
sections of the rendered Hydra YAML (paths/data/mlflow are platform-injected),
this module rejects two classes of keys:

1. **Hydra meta-fields** anywhere in the params tree: ``_target_``,
   ``_partial_``, ``_args_``, ``_recursive_``. These are how Hydra knows to
   ``importlib.import_module`` a target — letting users override one means
   arbitrary remote code execution inside the detector container.
2. **Platform-controlled top-level keys**: ``paths``, ``data``, ``mlflow``.
   These are written by ``services/job_config.JobConfigRenderer`` and must
   not be overridable.

Allowlist rather than blocklist would be safer, but maldet's per-detector
config trees are open-ended (every detector author can name new sections
freely), and a strict allowlist would force every detector to declare its
configurable surface area. Phase 11c trades that for a tight blocklist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HYDRA_META_KEYS = frozenset({"_target_", "_partial_", "_args_", "_recursive_"})
PLATFORM_RESERVED_PREFIXES = frozenset({"paths", "data", "mlflow"})


class UserParamsRejected(ValueError):
    """Raised on a forbidden key in user-submitted Hydra params."""


def validate_user_params(params: Any) -> None:
    """Recursively validate ``params``; raise on any forbidden key.

    Accepts both dotted-flat (``"model.n_estimators": 1``) and nested
    (``"model": {"n_estimators": 1}``) shapes — both are how lolday users
    pass overrides today.
    """
    if not isinstance(params, Mapping):
        raise UserParamsRejected(
            f"user params must be a dict, got {type(params).__name__}"
        )
    for key, val in params.items():
        if not isinstance(key, str):
            raise UserParamsRejected(
                f"user param keys must be strings, got {type(key).__name__}: {key!r}"
            )
        _check_key_is_safe(key)
        if isinstance(val, Mapping):
            _walk(val, parents=(key,))


def _check_key_is_safe(key: str) -> None:
    parts = key.split(".")
    if parts[0] in PLATFORM_RESERVED_PREFIXES:
        raise UserParamsRejected(
            f"key {key!r} starts with platform-reserved prefix {parts[0]!r}; "
            f"reserved={sorted(PLATFORM_RESERVED_PREFIXES)}"
        )
    for part in parts:
        if part in HYDRA_META_KEYS:
            raise UserParamsRejected(
                f"key {key!r} contains forbidden Hydra meta-field {part!r}; "
                f"forbidden={sorted(HYDRA_META_KEYS)}"
            )


def _walk(node: Mapping[str, Any], *, parents: tuple[str, ...]) -> None:
    for key, val in node.items():
        if not isinstance(key, str):
            raise UserParamsRejected(
                f"nested key under {'.'.join(parents)!r} must be a string"
            )
        if key in HYDRA_META_KEYS:
            raise UserParamsRejected(
                f"key {'.'.join((*parents, key))!r} is forbidden Hydra meta-field {key!r}"
            )
        if isinstance(val, Mapping):
            _walk(val, parents=(*parents, key))
```

- [ ] **Step 4: Run tests — pass**

```bash
uv run pytest tests/test_services_jobs_params_guard.py -v
```

Expected: all 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/jobs_params_guard.py backend/tests/test_services_jobs_params_guard.py
git commit -m "feat(jobs): phase 11c — Hydra-meta + platform-prefix user-params guard"
```

### Task G-2: Wire into jobs router

**Files:**

- Modify: `backend/app/routers/jobs.py`
- Modify: `backend/tests/test_routers_jobs.py` (or wherever the affected tests live)

- [ ] **Step 1: Wire the call**

In `backend/app/routers/jobs.py`, just below where the (now-deleted) jsonschema block used to be, add:

```python
from app.services.jobs_params_guard import UserParamsRejected, validate_user_params  # noqa: E402

# ... inside the job-submission handler, AFTER detector-version lookup:
try:
    validate_user_params(body.params)
except UserParamsRejected as e:
    raise HTTPException(status_code=422, detail=str(e))
```

(Adjust import location to follow the file's existing import pattern; the inline `noqa` is only needed if the existing imports use the standard top-of-file block.)

- [ ] **Step 2: Update jobs router tests**

In whatever test file submits to `POST /api/v1/jobs`, add three new cases:

```python
@pytest.mark.asyncio
async def test_submit_job_rejects_target_override(client, fixtures) -> None:
    resp = await client.post(
        "/api/v1/jobs",
        json={
            **fixtures.job_payload(),
            "params": {"model": {"_target_": "evil.module.func"}},
        },
        headers=fixtures.auth_headers(),
    )
    assert resp.status_code == 422
    assert "_target_" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_submit_job_rejects_paths_override(client, fixtures) -> None:
    resp = await client.post(
        "/api/v1/jobs",
        json={
            **fixtures.job_payload(),
            "params": {"paths.output_dir": "/anywhere"},
        },
        headers=fixtures.auth_headers(),
    )
    assert resp.status_code == 422
    assert "paths" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_submit_job_accepts_simple_hyperparam_override(client, fixtures) -> None:
    resp = await client.post(
        "/api/v1/jobs",
        json={
            **fixtures.job_payload(),
            "params": {"model": {"n_estimators": 500}},
        },
        headers=fixtures.auth_headers(),
    )
    assert resp.status_code in (200, 201, 202)
```

(Replace `fixtures.job_payload()` and `fixtures.auth_headers()` with the test fixture pattern this repo uses — copy from an existing passing test.)

Also delete any tests previously asserting jsonschema behaviour (e.g., asserting `"params invalid:"` from `jsonschema.validate`).

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_routers_jobs.py -v
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/jobs.py backend/tests/test_routers_jobs.py
git commit -m "feat(jobs): phase 11c — wire validate_user_params into job-submit endpoint"
```

---

## Part H — Helm chart + image bumps (Stream L)

### Task H-1: Build + push build-helper:v3

**Files:** none (operator step, but the agent prepares the commands).

- [ ] **Step 1: Build the image**

```bash
cd ~/Documents/repositories/lolday/charts/lolday/helpers/build-helper
docker build -t harbor.harbor.svc:80/lolday/build-helper:v3 .
docker inspect --format '{{.Config.Cmd}}' harbor.harbor.svc:80/lolday/build-helper:v3
```

Expected: `Cmd` is `[maldet_validator]`. Image builds cleanly.

- [ ] **Step 2: Push to Harbor**

```bash
docker login harbor.harbor.svc:80
docker push harbor.harbor.svc:80/lolday/build-helper:v3
```

(Operator action — needs cluster route to Harbor.)

### Task H-2: Bump references in lolday config

**Files:**

- Modify: `backend/app/config.py`
- Modify: `charts/lolday/Chart.yaml`
- Modify: `charts/lolday/values.yaml` (only if the build-helper image tag is templated there; otherwise skip)

- [ ] **Step 1: Bump `BUILD_IMAGE_HELPER`**

In `backend/app/config.py:18`, change:

```python
BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v2"
```

to:

```python
BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v3"
```

- [ ] **Step 2: Bump chart**

In `charts/lolday/Chart.yaml`:

```yaml
version: 0.14.0
appVersion: "phase11c"
```

(was `0.13.0` / `phase11b`).

- [ ] **Step 3: Check `values.yaml` for any build-helper tag references**

```bash
grep -n "build-helper" charts/lolday/values.yaml
```

If any matches, bump them to `v3`. Otherwise skip.

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py charts/lolday/Chart.yaml charts/lolday/values.yaml
git commit -m "chore: phase 11c — bump build-helper image to v3, chart 0.14.0"
```

---

## Part I — Lolday PR (Stream L)

### Task I-1: Full test suite + final ruff/mypy

- [ ] **Step 1: Run full backend tests**

```bash
cd ~/Documents/repositories/lolday/backend
uv run pytest -q
```

Expected: full green. If any failures, fix before committing further.

- [ ] **Step 2: Run lint**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

- [ ] **Step 3: Run build-helper tests**

```bash
cd ~/Documents/repositories/lolday/charts/lolday/helpers/build-helper
. .venv/bin/activate && pytest -v && deactivate
```

### Task I-2: Push + open PR

- [ ] **Step 1: Push branch**

```bash
cd ~/Documents/repositories/lolday
git push -u origin phase-11c-impl
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base main --head phase-11c-impl \
  --title "feat: phase 11c — detector contract migration to v2 (validators + build-args + cleanup)" \
  --body "$(cat <<'EOF'
## Summary

Migrates the lolday detector contract from v0 (\`BaseDetector\` ABC + per-detector pydantic JSON schema) to v2 (\`maldet.toml\` + OCI manifest label) end-to-end. Breaking change — no compat shim.

### Backend

- **Validator rewrite:** \`validate_repo_static\` now requires + parses \`maldet.toml\` via \`maldet.manifest.load_manifest\`. \`_check_base_detector_import\` is deleted.
- **Build pipeline:** the validate initContainer writes \`MALDET_NAME / VERSION / FRAMEWORK / MANIFEST_B64 / GIT_COMMIT\` to a shared \`build-args\` EmptyDir; the buildkit container reads them and emits \`--opt build-arg:KEY=VAL\`. v2 detectors now produce images with non-empty \`io.maldet.manifest\` labels.
- **Schema POST flow deleted:** \`/api/v1/internal/builds/{id}/schema\`, \`detector_build.pending_schema\`, \`detector_version.config_schema\`, and \`jsonschema.validate(body.params, dv.config_schema)\` are all removed.
- **Hydra params guard:** \`validate_user_params\` rejects \`_target_/_partial_/_args_/_recursive_\` (Hydra meta — would let users RCE) and \`paths./data./mlflow.\` prefixes (platform-controlled). Replaces the now-deleted jsonschema validation.
- Alembic migration drops the two columns. Ruff/mypy clean.

### Build-helper

- \`maldet_validator.py\` rewritten to be manifest-driven (no AST, no pydantic schema extraction, no HTTP POST). Image bumped to \`build-helper:v3\` with \`maldet[lightning] >= 1.0\` preinstalled.

### Chart

- \`0.13.0\` → \`0.14.0\`, \`appVersion: phase11c\`.

## Breaking changes

- v0 detectors (no \`maldet.toml\`) are rejected at registration.
- API: \`POST /api/v1/jobs\` now 422s on \`_target_\` and platform-prefix overrides instead of pydantic schema mismatches.

## Test plan

- [x] Backend test suite green (\`uv run pytest -q\`)
- [x] Build-helper unit tests green
- [x] Ruff/mypy clean
- [ ] CI green on PR
- [ ] (Operator) deploy to server30 — verify Alembic migration applies cleanly
- [ ] (Operator) build elfrfdet:v2.0.0 + elfcnndet:v2.0.0 via the lolday pipeline (Part J of plan)
- [ ] (Operator) train + evaluate + predict E2E

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Capture URL**

```bash
gh pr view --json number,url -q '.url'
```

### Task I-3: Operator merge checkpoint

> **CHECKPOINT.** Operator reviews + merges the PR in GitHub UI. After merge, the agent runs:

```bash
git checkout main
git pull --ff-only
git log --oneline -5
```

---

## Part R — elfrfdet v2.0.0 (Stream R, parallel to Stream L)

### Task R-1: Wipe + scaffold

- [ ] **Step 1: Wipe v0**

```bash
cd ~/Documents/repositories/elfrfdet
git ls-files | grep -v '^LICENSE$' | xargs git rm -f --quiet
find . -mindepth 1 -maxdepth 1 ! -name '.git' ! -name 'LICENSE' -exec rm -rf {} +
```

- [ ] **Step 2: Scaffold rf**

```bash
maldet scaffold --template rf --name elfrfdet --out .
```

- [ ] **Step 3: Sanity-check**

```bash
test -f maldet.toml && test -f Dockerfile && test -f pyproject.toml
test -f src/elfrfdet/{__init__.py,features.py,models.py}
grep -q '^name = "elfrfdet"$' maldet.toml
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: phase 11c step 1 — wipe v0 and scaffold rf template (elfrfdet)"
```

### Task R-2: Specialize maldet.toml + pyproject.toml + **init**.py

**Files:** `maldet.toml`, `pyproject.toml`, `src/elfrfdet/__init__.py`.

- [ ] **Step 1: Bump `maldet.toml`**

Edit the `[detector]` block:

```toml
[detector]
name = "elfrfdet"
version = "2.0.0"
framework = "sklearn"
description = "Random Forest ELF malware detector — reference template for the lolday platform on the maldet 1.0 framework"
```

- [ ] **Step 2: Replace `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "elfrfdet"
version = "2.0.0"
description = "Random Forest ELF malware detector — reference template for the lolday platform on the maldet 1.0 framework"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.12"
authors = [{ name = "bolin8017" }]
keywords = ["malware", "detection", "elf", "random-forest", "machine-learning", "maldet"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Topic :: Security",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]
dependencies = [
    "maldet>=1.0,<2.0",
    "scikit-learn>=1.4",
    "pyelftools>=0.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.1", "ruff>=0.6"]

[project.urls]
Homepage = "https://github.com/bolin8017/elfrfdet"
Repository = "https://github.com/bolin8017/elfrfdet.git"
Issues = "https://github.com/bolin8017/elfrfdet/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/elfrfdet"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- [ ] **Step 3: `__init__.py` (no shim — Phase 11c validator reads `maldet.toml`)**

```python
"""elfrfdet — Random Forest ELF malware detector on the maldet 1.0 framework."""

__version__ = "2.0.0"
```

- [ ] **Step 4: Verify install + maldet check**

```bash
python -m venv /tmp/elfrfdet-check && . /tmp/elfrfdet-check/bin/activate
pip install --quiet -e .
maldet check
deactivate
rm -rf /tmp/elfrfdet-check
```

Expect `OK`.

- [ ] **Step 5: Commit**

```bash
git add maldet.toml pyproject.toml src/elfrfdet/__init__.py
git commit -m "feat: phase 11c step 2 — specialize manifest+pyproject+__init__ (elfrfdet)"
```

### Task R-3: README + CHANGELOG + .gitignore

- [ ] **Step 1: Overwrite `README.md`**

````markdown
# elfrfdet

Random Forest malware detector for Linux ELF binaries. Feature = first 256 bytes of the `.text` section, as a `uint8` vector. Reference template for the [lolday](https://github.com/louiskyee/lolday) platform on the [maldet 1.0](https://github.com/bolin8017/maldet) framework.

## Install

```bash
pip install -e .[dev]
maldet check
maldet describe
```
````

## CLI

```bash
maldet run train    --config config.yaml
maldet run evaluate --config config.yaml
maldet run predict  --config config.yaml
```

A minimal `config.yaml` for local smoke-testing:

```yaml
defaults: [_self_]
stage: train
paths:
  config_dir: ${oc.env:PWD}
  output_dir: /tmp/elfrfdet-out
  samples_root: /path/to/samples # <sha[:2]>/<sha> layout
  source_model: /tmp/elfrfdet-out/model
data:
  train_csv: /path/to/train.csv # columns: file_name,label[,family]
  test_csv: /path/to/test.csv
  predict_csv: /path/to/predict.csv
model:
  _target_: sklearn.ensemble.RandomForestClassifier
  n_estimators: 100
  random_state: 42
```

## Dataset format

CSV with columns `file_name,label[,family]`. `file_name` is a SHA-256 hex string; the actual ELF lives at `<samples_root>/<sha[:2]>/<sha>`. `label` is `Malware` or `Benign`.

## How it works

1. **Feature extraction** (`src/elfrfdet/features.py::Text256Extractor`): open each ELF with `pyelftools`, read `.text.data()[:256]`, zero-pad to 256 bytes if shorter. Both the constructor _and_ `get_section_by_name(...)` are wrapped in `try/except` because pyelftools lazy-parses the section-header string table — `ELFParseError` can fire on the section access, not on the `ELFFile(f)` call.
2. **Model** (`src/elfrfdet/models.py::make_rf`): `sklearn.ensemble.RandomForestClassifier`, default `n_estimators=100`.
3. **Output**: `model/model.joblib`, `metrics.json`, `predictions.csv`, `events.jsonl` under `paths.output_dir`.

## On lolday

1. Register: `POST /api/v1/detectors { git_url: "https://github.com/bolin8017/elfrfdet.git" }` — Phase 11c validator parses `maldet.toml` and creates the Detector row.
2. Build a tag: `POST /api/v1/detectors/{id}/builds { git_tag: "v2.0.0" }`.
3. Submit a job: `POST /api/v1/jobs { type: "train", resource_profile: "standard", ... }`. With `manifest.resources.supports = ["cpu"]`, only `standard` (cpu) jobs are accepted; multi-GPU is rejected by `validate_job_submission`.

## Migrating from v0.1.x

v2 is a full rewrite on the maldet 1.0 framework — incompatible with v0's `BaseDetector` ABC. The v0 `ElfRfDetectorConfig` Pydantic model is gone; configuration flows through Hydra YAML and `maldet.toml`. The v0 `elfrfdet` CLI command no longer exists; use `maldet run <stage>`. v0 tags (`v0.1.1` and earlier) remain on this repo for historical reference but are deprecated.

## License

MIT

````

- [ ] **Step 2: `CHANGELOG.md`**

```markdown
# Changelog

## [2.0.0] - 2026-04-26

### Breaking

- Full rewrite on top of [maldet 1.0](https://pypi.org/project/maldet/).
- Removed: v0 `BaseDetector` ABC, `ElfRfDetectorConfig` pydantic model, per-detector `elfrfdet` CLI.
- Configuration is now Hydra YAML + `maldet.toml`. CLI is `maldet run train|evaluate|predict --config <yaml>`.
- Dockerfile expects build-time args (`MALDET_NAME`, `MALDET_VERSION`, `MALDET_FRAMEWORK`, `MALDET_MANIFEST_B64`, `GIT_COMMIT`) emitted as OCI image labels — required by lolday Phase 11c's pipeline.

## [0.1.1] - 2026-(prior)

Final v0 release on the `islab-malware-detector` framework. Deprecated.
````

- [ ] **Step 3: `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
dist/
build/
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
htmlcov/
.coverage
*.joblib
*.ckpt
events.jsonl
metrics.json
predictions.csv
.DS_Store
```

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md .gitignore
git commit -m "docs: phase 11c step 3 — README+CHANGELOG+.gitignore (elfrfdet)"
```

### Task R-4: Tests — features

**Files:** `tests/__init__.py`, `tests/test_features.py`.

- [ ] **Step 1: Empty package marker**

```bash
mkdir -p tests
: > tests/__init__.py
```

- [ ] **Step 2: Write `tests/test_features.py`**

```python
"""Unit tests for elfrfdet.features.Text256Extractor."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from elfrfdet.features import Text256Extractor
from maldet.types import Sample


def _system_elf() -> Path:
    for candidate in ("/bin/ls", "/usr/bin/ls", "/bin/cat", "/usr/bin/cat"):
        p = Path(candidate)
        if p.is_file():
            return p
    pytest.skip("no system ELF available")


def _sample(path: Path) -> Sample:
    return Sample(sha256="0" * 64, path=path, label="Benign")


def test_returns_uint8_vector_of_default_size() -> None:
    extractor = Text256Extractor()
    vec = extractor.extract(_sample(_system_elf()))
    assert vec.dtype == np.uint8
    assert vec.shape == (256,)


def test_zero_pads_short_text() -> None:
    extractor = Text256Extractor(size=8192, pad_value=0)
    vec = extractor.extract(_sample(_system_elf()))
    assert vec.shape == (8192,)
    # Some trailing bytes must be padding zeros (real .text rarely fills 8192).
    assert vec[-256:].sum() < 256 * 255


def test_truncated_elf_raises_value_error(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.elf"
    truncated.write_bytes(b"\x7fELF" + b"\x00" * 12 + b"\x99")
    extractor = Text256Extractor()
    with pytest.raises(ValueError, match="ELF parse failed"):
        extractor.extract(_sample(truncated))


def test_no_text_section_raises_value_error(tmp_path: Path) -> None:
    not_elf = tmp_path / "not_an_elf.bin"
    not_elf.write_bytes(b"this is not an ELF binary")
    extractor = Text256Extractor()
    with pytest.raises(ValueError, match="ELF parse failed"):
        extractor.extract(_sample(not_elf))
```

- [ ] **Step 3: Run + commit**

```bash
pip install -e .[dev] --quiet
pytest tests/test_features.py -v
git add tests/__init__.py tests/test_features.py
git commit -m "test: phase 11c step 4 — Text256Extractor unit tests (elfrfdet)"
```

### Task R-5: Tests — manifest

**Files:** `tests/test_manifest.py`.

- [ ] **Step 1: Write**

```python
"""Tests for maldet.toml shape — guard against accidental drift."""

from __future__ import annotations

from pathlib import Path

from maldet.manifest import load_manifest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_loads_via_maldet() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.detector.name == "elfrfdet"
    assert m.detector.version == "2.0.0"
    assert m.detector.framework == "sklearn"


def test_manifest_resources_cpu_only() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.resources.supports == ["cpu"]
    assert m.resources.gpu_required is False


def test_manifest_lifecycle_no_distributed() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.lifecycle.supports_distributed is False
    assert set(m.lifecycle.stages) == {"train", "evaluate", "predict"}


def test_manifest_stages_reference_local_extractor() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    train = m.stages["train"]
    assert train.extractor == "elfrfdet.features:Text256Extractor"
    assert train.model == "elfrfdet.models:make_rf"
    assert train.trainer == "maldet.trainers.sklearn_trainer:SklearnTrainer"
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/test_manifest.py -v
git add tests/test_manifest.py
git commit -m "test: phase 11c step 5 — maldet.toml shape tests (elfrfdet)"
```

### Task R-6: GitHub Actions CI

**Files:** `.github/workflows/ci.yml`.

```yaml
name: CI

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          python -m pip install --upgrade pip
          pip install -e .[dev]
      - run: maldet check
      - run: pytest -v

  ruff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check .
      - run: ruff format --check .
```

- [ ] **Step 1: Verify ruff locally**

```bash
pip install --quiet ruff
ruff check .
ruff format --check .
```

- [ ] **Step 2: Commit**

```bash
mkdir -p .github/workflows
git add .github/workflows/ci.yml
git commit -m "ci: phase 11c step 6 — GitHub Actions (test + ruff) (elfrfdet)"
```

### Task R-7: Push + PR

- [ ] **Step 1: Push**

```bash
cd ~/Documents/repositories/elfrfdet
git push -u origin phase-11c-v2-rewrite
```

- [ ] **Step 2: PR**

```bash
gh pr create --repo bolin8017/elfrfdet --base main --head phase-11c-v2-rewrite \
  --title "feat: v2.0.0 — full rewrite on the maldet 1.0 framework" \
  --body "$(cat <<'EOF'
## Summary

Full rewrite of \`elfrfdet\` on top of [maldet 1.0](https://pypi.org/project/maldet/), replacing the v0 \`BaseDetector\` framework. Dockerfile + maldet.toml drive lolday Phase 11c's manifest-driven pipeline; no \`import maldet\` shim needed.

## Breaking change

v2 line. v0.1.x deprecated; tags retained for history.

## Test plan

- [ ] \`maldet check\` passes locally
- [ ] \`pytest\` green
- [ ] CI green
- [ ] (Phase 11c) build via lolday pipeline produces an image whose \`io.maldet.manifest\` label decodes to a valid \`DetectorManifest\`
- [ ] (Phase 11c) train + evaluate + predict E2E

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Task R-8: Operator merge + tag v2.0.0

> **CHECKPOINT.** Operator merges PR. Then:

```bash
cd ~/Documents/repositories/elfrfdet
git checkout main && git pull --ff-only
git tag -a v2.0.0 -m "v2.0.0 — full rewrite on the maldet 1.0 framework (Phase 11c)"
git push origin v2.0.0
```

---

## Part C2 — elfcnndet v2.0.0 (Stream C, parallel)

Same shape as Part R; reproduced fully so engineers reading C2 don't need to flip back to R.

### Task C2-1: Wipe + scaffold

```bash
cd ~/Documents/repositories/elfcnndet
git ls-files | grep -v '^LICENSE$' | xargs git rm -f --quiet
find . -mindepth 1 -maxdepth 1 ! -name '.git' ! -name 'LICENSE' -exec rm -rf {} +
maldet scaffold --template cnn --name elfcnndet --out .
test -f maldet.toml && grep -q '^framework = "lightning"$' maldet.toml
git add -A
git commit -m "feat: phase 11c step 1 — wipe v0 and scaffold cnn template (elfcnndet)"
```

### Task C2-2: Specialize maldet.toml + pyproject.toml + **init**.py

`maldet.toml` `[detector]`:

```toml
[detector]
name = "elfcnndet"
version = "2.0.0"
framework = "lightning"
description = "1D-CNN ELF malware detector with PyTorch Lightning DDP — reference template for the lolday platform on the maldet 1.0 framework"
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[project]
name = "elfcnndet"
version = "2.0.0"
description = "1D-CNN ELF malware detector with PyTorch Lightning DDP — reference template for the lolday platform on the maldet 1.0 framework"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.12"
authors = [{ name = "bolin8017" }]
keywords = ["malware", "detection", "elf", "pytorch", "lightning", "deep-learning", "gpu", "maldet"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Topic :: Security",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]
dependencies = [
    "maldet[lightning]>=1.0,<2.0",
    "pyelftools>=0.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.1", "ruff>=0.6"]

[project.urls]
Homepage = "https://github.com/bolin8017/elfcnndet"
Repository = "https://github.com/bolin8017/elfcnndet.git"
Issues = "https://github.com/bolin8017/elfcnndet/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/elfcnndet"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

`src/elfcnndet/__init__.py`:

```python
"""elfcnndet — 1D-CNN ELF malware detector on the maldet 1.0 framework."""

__version__ = "2.0.0"
```

```bash
python -m venv /tmp/elfcnndet-check && . /tmp/elfcnndet-check/bin/activate
pip install --quiet -e .
maldet check
deactivate && rm -rf /tmp/elfcnndet-check
git add maldet.toml pyproject.toml src/elfcnndet/__init__.py
git commit -m "feat: phase 11c step 2 — specialize manifest+pyproject+__init__ (elfcnndet)"
```

### Task C2-3: README + CHANGELOG + .gitignore

`README.md`:

````markdown
# elfcnndet

1D-CNN malware detector for Linux ELF binaries with PyTorch Lightning. Feature = first 256 bytes of the `.text` section, fed through a byte-embedding + conv stack. Multi-GPU training via Lightning **DDP** (replaces the v0 `nn.DataParallel` pattern). Reference template for the [lolday](https://github.com/louiskyee/lolday) platform on the [maldet 1.0](https://github.com/bolin8017/maldet) framework.

## Install

```bash
pip install -e .[dev]
maldet check
maldet describe
```
````

## CLI

```bash
maldet run train    --config config.yaml
maldet run evaluate --config config.yaml
maldet run predict  --config config.yaml
```

## Architecture

```
input bytes (N, 256) uint8
    ↓ nn.Embedding(256 → 32)
    ↓ Conv1d(32 → 64, kernel=5) + ReLU + MaxPool1d(2)
    ↓ Conv1d(64 → 128, kernel=3) + ReLU
    ↓ AdaptiveAvgPool1d(1)
    ↓ Linear(128 → 2)
    ↓ CrossEntropyLoss
```

`ByteCNN(LightningModule)` in `src/elfcnndet/models.py`.

## Distributed training (DDP)

`maldet.toml` declares `lifecycle.supports_distributed = "ddp"`. When lolday's backend submits a `gpu2` resource profile, `maldet.trainers.lightning_trainer` reads `MALDET_GPU_COUNT=2` + `MALDET_DISTRIBUTED_STRATEGY=ddp` from the env and configures `Trainer(strategy="ddp", devices=2)`. No more hand-rolled `nn.DataParallel`.

## On lolday

1. Register: `POST /api/v1/detectors { git_url: "https://github.com/bolin8017/elfcnndet.git" }`.
2. Build a tag: `POST /api/v1/detectors/{id}/builds { git_tag: "v2.0.0" }`.
3. Submit a job: `POST /api/v1/jobs { type: "train", resource_profile: "gpu2", ... }`. Phase 11b's `validate_job_submission` permits the multi-GPU profile because `manifest.lifecycle.supports_distributed = "ddp"`.

## Migrating from v0.2.x

v2 is a full rewrite. v0 \`BaseDetector\`, \`ElfCnnDetectorConfig\`, per-detector CLI, and runtime \`nn.DataParallel\` wrapping are all removed. Use \`maldet run <stage>\` and let Lightning's \`strategy="ddp"\` handle multi-GPU.

## License

MIT

````

`CHANGELOG.md`:

```markdown
# Changelog

## [2.0.0] - 2026-04-26

### Breaking

- Full rewrite on top of [maldet 1.0](https://pypi.org/project/maldet/) using PyTorch Lightning.
- DDP (Lightning's `strategy="ddp"`) replaces v0's runtime `nn.DataParallel` wrapping.
- Removed: v0 `BaseDetector` ABC, `ElfCnnDetectorConfig` pydantic model, per-detector `elfcnndet` CLI, hand-rolled training loop.
- Dockerfile expects build-time args (`MALDET_*`, `GIT_COMMIT`) emitted as OCI labels for lolday Phase 11c's pipeline.

## [0.2.1] - 2026-(prior)

Final v0 release on \`islab-malware-detector\` + \`nn.DataParallel\`. Deprecated.
````

`.gitignore`: same as elfrfdet (Task R-3 step 3 — copy verbatim).

```bash
git add README.md CHANGELOG.md .gitignore
git commit -m "docs: phase 11c step 3 — README+CHANGELOG+.gitignore (elfcnndet)"
```

### Task C2-4: Tests — features

`tests/__init__.py` empty; `tests/test_features.py` is the same as Task R-4 Step 2 with `from elfcnndet.features import Text256Extractor`. Run:

```bash
pip install -e .[dev] --quiet
pytest tests/test_features.py -v
git add tests/__init__.py tests/test_features.py
git commit -m "test: phase 11c step 4 — Text256Extractor unit tests (elfcnndet)"
```

### Task C2-5: Tests — manifest + model

`tests/test_manifest.py`:

```python
"""Tests for maldet.toml shape — guard against accidental drift."""

from __future__ import annotations

from pathlib import Path

from maldet.manifest import load_manifest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_manifest_loads_via_maldet() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.detector.name == "elfcnndet"
    assert m.detector.version == "2.0.0"
    assert m.detector.framework == "lightning"


def test_manifest_resources_supports_multi_gpu() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.resources.supports == ["cpu", "gpu1", "gpu2"]


def test_manifest_lifecycle_supports_ddp() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    assert m.lifecycle.supports_distributed == "ddp"
    assert set(m.lifecycle.stages) == {"train", "evaluate", "predict"}


def test_manifest_stages_reference_local_extractor_and_lightning_trainer() -> None:
    m = load_manifest(REPO_ROOT / "maldet.toml")
    train = m.stages["train"]
    assert train.extractor == "elfcnndet.features:Text256Extractor"
    assert train.model == "elfcnndet.models:make_cnn"
    assert train.trainer == "maldet.trainers.lightning_trainer:LightningTrainer"
```

`tests/test_models.py`:

```python
"""Smoke test the model factory shape."""

from __future__ import annotations

import lightning.pytorch as pl
import torch

from elfcnndet.models import make_cnn


def test_make_cnn_returns_lightning_module() -> None:
    assert isinstance(make_cnn(), pl.LightningModule)


def test_forward_accepts_uint8_byte_indices() -> None:
    model = make_cnn().eval()
    x = torch.randint(0, 256, (4, 256), dtype=torch.long)
    with torch.inference_mode():
        out = model(x)
    assert out.shape == (4, 2)
```

```bash
pytest tests/test_manifest.py tests/test_models.py -v
git add tests/test_manifest.py tests/test_models.py
git commit -m "test: phase 11c step 5 — manifest + model factory tests (elfcnndet)"
```

### Task C2-6: GitHub Actions

Same `.github/workflows/ci.yml` content as Task R-6 — copy verbatim.

```bash
pip install --quiet ruff && ruff check . && ruff format --check .
mkdir -p .github/workflows
# write the file (same content as Task R-6 Step 1)
git add .github/workflows/ci.yml
git commit -m "ci: phase 11c step 6 — GitHub Actions (test + ruff) (elfcnndet)"
```

### Task C2-7: Push + PR

```bash
git push -u origin phase-11c-v2-rewrite

gh pr create --repo bolin8017/elfcnndet --base main --head phase-11c-v2-rewrite \
  --title "feat: v2.0.0 — full rewrite on the maldet 1.0 framework (DDP)" \
  --body "$(cat <<'EOF'
## Summary

Full rewrite of \`elfcnndet\` on top of [maldet 1.0](https://pypi.org/project/maldet/) using PyTorch Lightning. **DDP replaces v0's runtime \`nn.DataParallel\` wrapping.**

## Breaking change

v2 line. v0.2.x deprecated.

## Test plan

- [ ] \`maldet check\` passes locally
- [ ] \`pytest\` green
- [ ] CI green
- [ ] (Phase 11c) build via lolday pipeline produces an image whose \`io.maldet.manifest\` label decodes to a valid \`DetectorManifest\` with \`framework == "lightning"\` and \`supports_distributed == "ddp"\`
- [ ] (Phase 11c) 2-GPU DDP training E2E

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Task C2-8: Operator merge + tag v2.0.0

> **CHECKPOINT.**

```bash
git checkout main && git pull --ff-only
git tag -a v2.0.0 -m "v2.0.0 — full rewrite on the maldet 1.0 framework (Phase 11c)"
git push origin v2.0.0
```

---

## Part J — Operator E2E (post-merge)

> **OPERATOR-DRIVEN.** All three PRs (lolday#…, elfrfdet#…, elfcnndet#…) merged + tags pushed. Now exercise the v2 pipeline end-to-end on server30.

### Task J-1: Deploy lolday phase11c chart

```bash
cd ~/Documents/repositories/lolday
source ~/.lolday-secrets.env
bash scripts/deploy.sh
kubectl -n lolday rollout status deploy/lolday-backend
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
  -c '\d detector_build' -c '\d detector_version' \
  | grep -E "pending_schema|config_schema" || echo "(both columns absent — migration applied)"
```

Expect: the two columns are NOT listed.

### Task J-2: Register both detectors

Through the frontend or curl:

```bash
TOKEN="<JWT from /admin>"
curl -fsS -X POST http://lolday.local/api/v1/detectors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"git_url": "https://github.com/bolin8017/elfrfdet.git"}'
curl -fsS -X POST http://lolday.local/api/v1/detectors \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"git_url": "https://github.com/bolin8017/elfcnndet.git"}'
```

Expect: both succeed (200/201). The new `validate_repo_static` parses each repo's `maldet.toml` cleanly.

### Task J-3: Build elfrfdet:v2.0.0 via the pipeline

```bash
DETECTOR_ID="<from registration response>"
curl -fsS -X POST "http://lolday.local/api/v1/detectors/$DETECTOR_ID/builds" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"git_tag": "v2.0.0"}'
# Watch:
kubectl -n lolday-build logs -f -l lolday.io/build-id=<build-id> --all-containers=true
```

Expect: clone → validate (writes 5 build-args files) → buildkit (pushes to Harbor with non-empty labels) → succeed. Reconciler creates the `detector_version` row with `manifest` populated and no `config_schema`.

Verify the Harbor label decodes:

```bash
curl -fsS -u admin:$HARBOR_ADMIN_PASSWORD \
  "http://harbor.harbor.svc:80/api/v2.0/projects/detectors/repositories/elfrfdet/artifacts/v2.0.0" \
  | python -c 'import json,sys,base64;d=json.load(sys.stdin);b=d["extra_attrs"]["config"]["Labels"]["io.maldet.manifest"];print(json.loads(base64.b64decode(b))["detector"])'
```

### Task J-4: Build elfcnndet:v2.0.0

Same as J-3 with `elfcnndet`. Verify `framework == "lightning"`, `supports_distributed == "ddp"`.

### Task J-5: Train smoke test (CPU rf, 1 GPU sklearn-on-GPU not applicable; 2-GPU DDP for cnn)

```bash
# elfrfdet smoke train (small dataset)
curl -fsS -X POST http://lolday.local/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type": "train", "detector_version_id": "<rf-dv-id>", "resource_profile": "standard",
       "train_dataset_id": "<smoke-csv-id>", "params": {"model": {"n_estimators": 10}}}'
```

Expected: job goes through to `succeeded` via `stage_end` event. WS `/jobs/{id}/events` streams events to a browser-connected client.

```bash
# elfcnndet 2-GPU DDP smoke train
curl -fsS -X POST http://lolday.local/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type": "train", "detector_version_id": "<cnn-dv-id>", "resource_profile": "gpu2",
       "train_dataset_id": "<smoke-csv-id>", "params": {"trainer": {"max_epochs": 1}}}'
```

Expected: 2-GPU DDP run; metrics + events stream to UI.

### Task J-6: Negative-test the params guard

```bash
# Should 422 — _target_ override
curl -i -X POST http://lolday.local/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type": "train", "detector_version_id": "<rf-dv-id>", "resource_profile": "standard",
       "train_dataset_id": "<smoke-csv-id>",
       "params": {"model": {"_target_": "evil.module.func"}}}'
```

Expect: HTTP 422 with `_target_` in the response body.

```bash
# Should 422 — paths.* override
curl -i -X POST http://lolday.local/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type": "train", "detector_version_id": "<rf-dv-id>", "resource_profile": "standard",
       "train_dataset_id": "<smoke-csv-id>",
       "params": {"paths.output_dir": "/anywhere"}}'
```

Expect: HTTP 422.

---

## Part Z — Memory + Phase 11d scope reset

### Task Z-1: Update memory

**Files:**

- Modify: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_phase11_progress.md`
- Modify: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_elf_template_detectors.md`

In `project_phase11_progress.md`, replace the entire `## Phase 11c —` AND `## Phase 11d —` sections with:

```markdown
## Phase 11c — Detector contract migration to v2 + template detectors v2 (DONE 2026-04-26)

- lolday PR <URL>, squash <SHA>: validators rewritten manifest-driven (backend + build-helper); build pipeline emits `--opt build-arg:MALDET_*=…`; v0 schema POST flow + `pending_schema` + `config_schema` columns deleted; jobs router replaces jsonschema validation with `validate_user_params` (rejects Hydra meta + platform-prefix overrides); chart 0.14.0; build-helper image v3.
- elfrfdet v2.0.0 PR <URL>, tag pushed; built via lolday pipeline at `harbor.harbor.svc:80/detectors/elfrfdet:v2.0.0`.
- elfcnndet v2.0.0 PR <URL>, tag pushed; built via lolday pipeline at `harbor.harbor.svc:80/detectors/elfcnndet:v2.0.0`.
- E2E smoke completed: rf train+eval+predict on CPU profile; cnn 2-GPU DDP train.
- Plan: `lolday/docs/superpowers/plans/2026-04-26-phase11c-template-detectors-v2.md`

## Phase 11d — v0 retirement (PENDING)

- Hard-delete v0 rows in `detector` / `detector_version` / `build` tables (carefully — preserve audit log).
- Delete v0 Harbor artifacts (`detectors/elfrfdet:v0.1.x`, `detectors/elfcnndet:v0.2.x`, plus any other v0-tagged images).
- Archive `bolin8017/islab-malware-detector` GitHub repo.
- Mark the v0 PyPI release deprecated (or yank if appropriate).
- Post-mortem: `docs/phase11d-retirement-findings.md`.
```

In `project_elf_template_detectors.md`, replace the body with the v2 description (already written in the previous plan revision — see Task Z-2 step 2 of the prior file; reproduce verbatim, removing any references to "lolday build pipeline can't yet build v2 detectors" since it now can).

- [ ] **Step 1: Edit both memory files**

(See full content templates in the previous plan version under Z-2; the Phase 11d section is the only structural change vs. the previous plan since the manual-Harbor-push and validator-shim caveats are gone.)

- [ ] **Step 2: No git commit needed for memory files.**

---

## Self-review checklist

- **Spec coverage** (user's three points handled root-cause):
  - Item 1 (validator gap) → Part B (backend) + Part D (build-helper) — both rewritten manifest-driven, no shim. ✔
  - Item 2 (manual Harbor push) → Part E (build-args injection) — pipeline now produces correctly labelled images. ✔
  - Item 3 (memory / 11d scope) → Part Z — 11d shrunk to v0 retirement. ✔
- **Tech-debt purge:** schema POST route + `pending_schema` + `config_schema` + `jsonschema.validate` all deleted (Parts C, F). ✔
- **Security floor:** `validate_user_params` (Part G) replaces the deleted jsonschema validation, blocking `_target_` RCE + platform-prefix clobber. ✔
- **No `import maldet` shim** in detector repos. ✔
- **Tests** for every new function (validator, build-args, params guard) — TDD per writing-plans. ✔
- **Operator-driven steps** clearly marked: I-3 (lolday merge), R-8 (rf merge+tag), C2-8 (cnn merge+tag), Part J (E2E). ✔
- **Subagent parallelism explicit:** Streams L / R / C2 are independent until E2E. ✔
- **No placeholders.** ✔
- **Type consistency:** `validate_user_params`, `validate_manifest`, `write_build_args`, `Text256Extractor` names match across producer + tests. ✔

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase11c-template-detectors-v2.md`.

**Recommended:** Subagent-Driven (`superpowers:subagent-driven-development`) — three parallel agents on Streams L, R, C2; two-stage review per task; operator handles PR merges + Part J.

**Alternative:** Inline (`superpowers:executing-plans`) — one stream at a time with checkpoints.

Estimated wall-clock: 4–6 hours of subagent work for Streams L+R+C2 in parallel; then operator does Part J (~30 min if smoke datasets are pre-staged).
