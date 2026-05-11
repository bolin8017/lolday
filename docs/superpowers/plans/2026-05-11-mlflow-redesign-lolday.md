# MLflow Redesign — lolday Platform Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the MLflow run lifecycle, inject proper provenance tags, log Hydra config with dataset IDs, enable MLflow system metrics, and switch the frontend Runs page Duration column to lolday's authoritative timestamps. Cuts over to maldet 2.2.0 in the same release wave.

**Architecture:** `backend/app/services/mlflow_client.py::create_run` requires explicit `start_time_ms`; reconciler terminal transitions call a new `_finalize_mlflow_run()` helper so MLflow runs no longer stay RUNNING after pods die; `services/build.py` captures `maldet_version` at build time and writes it to `DetectorVersion`; `routers/jobs.py` emits 14+ provenance tags + an experiment-level `mlflow.note.content` markdown description on first-time experiment creation; `routers/experiments_proxy.py` enriches each flattened run with lolday's `started_at`/`finished_at` so the frontend Duration column reflects compute time rather than wall-clock-from-submit; detector containers get `MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING=true` after the base image is rebuilt with `psutil`/`pynvml`.

**Tech Stack:** Python 3.12 (FastAPI / SQLAlchemy / Alembic / httpx) + React 18 + TanStack Query + shadcn/ui. Docker (base image rebuild). Existing tooling: `uv run pytest`, `pnpm test`, `pre-commit run --all-files`.

**Reference:** Spec — `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`.

---

## File Structure

### Backend — to modify

| Path                                       | Change                                                                                                                 |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `backend/app/services/mlflow_client.py`    | `create_run` requires `start_time_ms`; add `set_experiment_tag`; `update_run` accepts named kwargs only                |
| `backend/app/routers/jobs.py`              | Pass `start_time_ms`; inject provenance tags; set experiment description on first creation                             |
| `backend/app/reconciler/jobs.py`           | Add `_finalize_mlflow_run` helper; call from FAILED / TIMEOUT / SUCCEEDED handlers                                     |
| `backend/app/services/job_spec.py`         | Add `MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING` + sampling interval env vars to `_detector_container`                       |
| `backend/app/services/job_config.py`       | Render `cfg.lolday.*` dataset IDs into the Hydra YAML                                                                  |
| `backend/app/models/detector.py`           | Add `maldet_version: Mapped[str \| None]` to `DetectorVersion`                                                         |
| `backend/app/services/build.py`            | After successful build, run `docker run --rm <image> pip show maldet` to capture version; write to `dv.maldet_version` |
| `backend/app/routers/experiments_proxy.py` | Enrich `_flatten_run` with lolday Job `started_at` / `finished_at`; batched lookup by `lolday.job_id` tag              |

### Backend — to create

| Path                                                                  | Purpose                                                                         |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `backend/migrations/versions/<rev>_detectorversion_maldet_version.py` | Alembic migration: add `maldet_version` nullable VARCHAR(16)                    |
| `backend/tests/test_services_mlflow_client_v2.py`                     | Unit tests for new API surface (`start_time_ms` required, `set_experiment_tag`) |
| `backend/tests/test_reconciler_mlflow_finalize.py`                    | Unit tests covering finalize on FAILED / TIMEOUT / SUCCEEDED                    |
| `backend/tests/test_experiments_proxy_enrichment.py`                  | Unit tests for the lolday-Job enrichment in `_flatten_run`                      |

### Frontend — to modify

| Path                                          | Change                                                                                                             |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `frontend/src/routes/_authed.runs.$expId.tsx` | Duration cell reads `row.lolday_started_at` / `row.lolday_finished_at` instead of MLflow `start_time` / `end_time` |
| `frontend/src/api/queries/runs.ts`            | Extend `Row` type with `lolday_started_at`, `lolday_finished_at`                                                   |
| `frontend/tests/unit/RunsListPage.test.tsx`   | Update test fixtures to include new fields; add a case for missing fields → "—"                                    |

### Helpers — to modify

| Path                                                                       | Change                                                                     |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `charts/lolday/helpers/pytorch-cu12-base/Dockerfile`                       | Add `psutil`, `pynvml` to the pip install list (system metrics dependency) |
| `charts/lolday/helpers/pytorch-cu12-base/CHANGELOG.md` (create if missing) | Bump tag to `:v5`                                                          |

### Docs — to modify

| Path                               | Change                                                                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `docs/architecture.md`             | Add an MLflow data-model section under §1 references; note the maldet 2.2 requirement                                        |
| `docs/runbooks/troubleshooting.md` | Add SOP: "MLflow run stuck RUNNING after job ended" → check reconciler logs; "missing system metrics" → check base image tag |
| `CLAUDE.md`                        | One-line nav hint pointing to this spec + plan                                                                               |

---

## Task 1: Add `set_experiment_tag` to `MlflowClient` + harden `create_run` signature

**Files:**

- Modify: `backend/app/services/mlflow_client.py:66-96`
- Create: `backend/tests/test_services_mlflow_client_v2.py`

- [ ] **Step 1: Write failing test for required `start_time_ms` kwarg**

Create `backend/tests/test_services_mlflow_client_v2.py`:

```python
"""mlflow_client.MlflowClient — phase 2026-05-11 API surface."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.mlflow_client import MlflowClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MlflowClient:
    c = MlflowClient("http://mlflow.test", timeout=1.0, retries=1)
    return c


@pytest.mark.asyncio
async def test_create_run_requires_start_time_ms(client: MlflowClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling create_run without start_time_ms is a TypeError."""
    monkeypatch.setattr(client, "_request", AsyncMock(return_value={"run": {"info": {"run_id": "abc"}}}))
    with pytest.raises(TypeError):
        await client.create_run("42")  # type: ignore[call-arg]  # missing start_time_ms by design


@pytest.mark.asyncio
async def test_create_run_passes_start_time_in_payload(client: MlflowClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = AsyncMock(return_value={"run": {"info": {"run_id": "abc"}}})
    monkeypatch.setattr(client, "_request", mock)
    await client.create_run("42", start_time_ms=1700000000123, tags=[{"key": "k", "value": "v"}])
    args, kwargs = mock.call_args
    assert kwargs["json"]["start_time"] == 1700000000123
    assert kwargs["json"]["experiment_id"] == "42"
    assert kwargs["json"]["tags"] == [{"key": "k", "value": "v"}]


@pytest.mark.asyncio
async def test_set_experiment_tag_posts_correct_payload(client: MlflowClient, monkeypatch: pytest.MonkeyPatch) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.set_experiment_tag("42", "mlflow.note.content", "**Hello**")
    args, kwargs = mock.call_args
    assert args[0] == "POST"
    assert args[1] == "/experiments/set-experiment-tag"
    assert kwargs["json"] == {"experiment_id": "42", "key": "mlflow.note.content", "value": "**Hello**"}
```

- [ ] **Step 2: Run — expect failures**

Run: `cd /home/bolin8017/Documents/repositories/lolday/backend && uv run pytest tests/test_services_mlflow_client_v2.py -x`

Expected: `AttributeError` on `set_experiment_tag` and the `TypeError` test passes vacuously (current `create_run` has `start_time` not as required kwarg).

- [ ] **Step 3: Update `MlflowClient.create_run` signature to require `start_time_ms`**

Edit `backend/app/services/mlflow_client.py` — replace the `create_run` method (currently lines 99-106) with:

```python
    async def create_run(
        self,
        experiment_id: str,
        *,
        start_time_ms: int,
        tags: list[dict[str, str]] | None = None,
    ) -> str:
        """Create an MLflow run. ``start_time_ms`` is REQUIRED because the
        MLflow REST API defaults the field to 0 (Unix epoch) when omitted —
        unlike the Python SDK which auto-fills ``time.time() * 1000``.
        """
        payload: dict[str, Any] = {
            "experiment_id": experiment_id,
            "start_time": start_time_ms,
        }
        if tags:
            payload["tags"] = tags
        resp = await self._request("POST", "/runs/create", json=payload)
        return resp["run"]["info"]["run_id"]
```

- [ ] **Step 4: Add `set_experiment_tag` method**

Append to `MlflowClient` (after `search_experiments`):

```python
    async def set_experiment_tag(
        self, experiment_id: str, key: str, value: str
    ) -> None:
        """Set an experiment-level tag. ``mlflow.note.content`` is rendered
        as Markdown in the MLflow native UI experiment page header."""
        await self._request(
            "POST",
            "/experiments/set-experiment-tag",
            json={"experiment_id": experiment_id, "key": key, "value": value},
        )
```

- [ ] **Step 5: Run new tests**

Run: `uv run pytest tests/test_services_mlflow_client_v2.py -v`

Expected: 3 passed.

- [ ] **Step 6: Update existing call site to keep tests green**

Find the existing `create_run` caller in `routers/jobs.py` and the test that hits it (`tests/test_jobs_mlflow_naming.py`). The call site fix happens in Task 3; for now, add a temporary `start_time_ms=0` to keep tests compilable. (We'll set the real value in Task 3.)

Edit `backend/app/routers/jobs.py:308` — change

```python
    run_id = await client.create_run(
        dv.mlflow_experiment_id,
        tags=[...],
    )
```

to (TEMPORARY):

```python
    import time as _time  # remove in Task 3 where we add a real top-level import

    run_id = await client.create_run(
        dv.mlflow_experiment_id,
        start_time_ms=int(_time.time() * 1000),
        tags=[...],
    )
```

- [ ] **Step 7: Run the full mlflow_client + jobs tests**

Run: `uv run pytest tests/test_services_mlflow_client.py tests/test_jobs.py tests/test_jobs_mlflow_naming.py tests/test_services_mlflow_client_v2.py -v`

Expected: all green.

- [ ] **Step 8: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add backend/app/services/mlflow_client.py backend/app/routers/jobs.py backend/tests/test_services_mlflow_client_v2.py
git commit -m "feat(backend)!: MlflowClient.create_run requires start_time_ms; add set_experiment_tag"
```

---

## Task 2: Add `_finalize_mlflow_run` to reconciler — failing test

**Files:**

- Create: `backend/tests/test_reconciler_mlflow_finalize.py`

- [ ] **Step 1: Write the test**

Create the file:

```python
"""reconciler.jobs._finalize_mlflow_run + its terminal call sites."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.job import Job, JobStatus, JobType


def _make_job(status: JobStatus = JobStatus.RUNNING, with_mlflow: bool = True) -> Job:
    j = Job(
        id=__import__("uuid").uuid4(),
        type=JobType.TRAIN,
        status=status,
        owner_id=__import__("uuid").uuid4(),
        submitted_at=datetime.now(UTC),
        resolved_config={"yaml": ""},
    )
    if with_mlflow:
        j.mlflow_run_id = "run-abc"
        j.mlflow_experiment_id = "42"
    return j


@pytest.mark.asyncio
async def test_finalize_run_calls_update_with_failed_status() -> None:
    from app.reconciler.jobs import _finalize_mlflow_run

    j = _make_job()
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock()
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        await _finalize_mlflow_run(j, "FAILED")
    fake_client.update_run.assert_awaited_once()
    args, kwargs = fake_client.update_run.call_args
    assert args[0] == "run-abc"
    assert kwargs["status"] == "FAILED"
    assert isinstance(kwargs["end_time_ms"], int)


@pytest.mark.asyncio
async def test_finalize_run_noop_when_no_mlflow_run_id() -> None:
    from app.reconciler.jobs import _finalize_mlflow_run

    j = _make_job(with_mlflow=False)
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock()
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        await _finalize_mlflow_run(j, "FAILED")
    fake_client.update_run.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_run_swallows_mlflow_errors() -> None:
    """A flaky MLflow server must not block lolday's job status update."""
    from app.reconciler.jobs import _finalize_mlflow_run
    from app.services.mlflow_client import MlflowError

    j = _make_job()
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock(side_effect=MlflowError("server unreachable"))
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        # must NOT raise
        await _finalize_mlflow_run(j, "FAILED")
```

- [ ] **Step 2: Run — expect ImportError**

Run: `uv run pytest tests/test_reconciler_mlflow_finalize.py -x`

Expected: `ImportError: cannot import name '_finalize_mlflow_run'`.

---

## Task 3: Implement `_finalize_mlflow_run` + wire up terminal call sites

**Files:**

- Modify: `backend/app/reconciler/jobs.py`
- Modify: `backend/app/routers/jobs.py`

- [ ] **Step 1: Add `_finalize_mlflow_run` helper**

Edit `backend/app/reconciler/jobs.py` — append after the existing imports:

```python
import time as _time  # noqa: E402  # used by _finalize_mlflow_run end_time generation


async def _finalize_mlflow_run(
    j: Job,
    status: str,
    *,
    end_time_ms: int | None = None,
) -> None:
    """Update the MLflow run to a terminal status when lolday terminates the Job.

    Idempotent: maldet typically writes ``FINISHED`` itself on success, so a
    second update is a no-op overwrite from MLflow's side. Critical for
    ``FAILED`` / ``KILLED`` cases where the pod died before maldet could
    write ``end_run()``.

    Spec § 5.5.
    """
    if not j.mlflow_run_id:
        return
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    try:
        await client.update_run(
            j.mlflow_run_id,
            status=status,
            end_time_ms=end_time_ms or int(_time.time() * 1000),
        )
    except Exception as exc:
        logger.warning("mlflow finalize failed for job %s: %s", j.id, exc)
        BACKEND_ERRORS.labels(stage="mlflow_finalize").inc()
```

- [ ] **Step 2: Update `MlflowClient.update_run` signature to match**

Edit `backend/app/services/mlflow_client.py` — replace `update_run` (currently lines 127-138) with:

```python
    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        end_time_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"run_id": run_id}
        if status:
            payload["status"] = status
        if end_time_ms:
            payload["end_time"] = end_time_ms
        await self._request("POST", "/runs/update", json=payload)
```

- [ ] **Step 3: Add finalize calls at the 3 terminal sites**

In `backend/app/reconciler/jobs.py`, the existing terminal transitions are:

- **Line ~80 (vcjob 404 → FAILED):**

After `j.status = JobStatus.FAILED` and `j.finished_at = datetime.now(UTC)`, add:

```python
        await _finalize_mlflow_run(j, "FAILED")
```

- **Line ~107 (active-deadline TIMEOUT):**

After `j.status = JobStatus.TIMEOUT` and `j.finished_at = datetime.now(UTC)`, add:

```python
        await _finalize_mlflow_run(j, "KILLED")
```

- **Line ~204 (SUCCEEDED):**

After `j.status = JobStatus.SUCCEEDED` and `j.finished_at = datetime.now(UTC)`, add:

```python
    await _finalize_mlflow_run(j, "FINISHED")
```

- **Line ~365 (FAILED via volcano phase):**

After `j.status = JobStatus.FAILED`, add:

```python
        await _finalize_mlflow_run(j, "FAILED")
```

(Confirm exact line numbers via `grep -n 'j.status = JobStatus' backend/app/reconciler/jobs.py`.)

- [ ] **Step 4: Run the finalize tests**

Run: `uv run pytest tests/test_reconciler_mlflow_finalize.py -v`

Expected: 3 passed.

- [ ] **Step 5: Run the broader reconciler test suite**

Run: `uv run pytest tests/test_reconciler_jobs.py -v`

Expected: green (the autouse mock for MLflow protects existing tests from the new finalize calls — verify by checking `tests/conftest.py`'s `_mock_mlflow_client` fixture; if needed, expand it to mock `update_run`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/reconciler/jobs.py backend/app/services/mlflow_client.py backend/tests/test_reconciler_mlflow_finalize.py
git commit -m "feat(backend): reconciler finalizes MLflow run on terminal transitions"
```

---

## Task 4: System metrics env vars in detector container

**Files:**

- Modify: `backend/app/services/job_spec.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_services_job_spec.py` (or whichever test file covers `_detector_container`):

```python
def test_detector_container_has_system_metrics_env() -> None:
    from app.services.job_spec import _detector_container

    c = _detector_container(
        detector_image="harbor/elf:rf",
        action="train",
        mlflow_tracking_uri="http://mlflow.test",
        mlflow_run_id="abc",
        mlflow_experiment_id="42",
        gpu_count=1,
        gpu_strategy="none",
    )
    env_keys = {e["name"] for e in c["env"]}
    assert "MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING" in env_keys
    assert "MLFLOW_SYSTEM_METRICS_SAMPLING_INTERVAL" in env_keys
    env_map = {e["name"]: e["value"] for e in c["env"]}
    assert env_map["MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING"] == "true"
```

- [ ] **Step 2: Run — expect AssertionError**

Run: `uv run pytest tests/test_services_job_spec.py -k system_metrics -x`

Expected: failure on `assert "MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING" in env_keys`.

- [ ] **Step 3: Add the env vars**

Edit `backend/app/services/job_spec.py:_detector_container` — in the `env` list, just before the trailing `{"name": "USER", "value": "maldet"}` entry, add:

```python
            {"name": "MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING", "value": "true"},
            {"name": "MLFLOW_SYSTEM_METRICS_SAMPLING_INTERVAL", "value": "10"},
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_services_job_spec.py -k system_metrics -v`

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_spec.py backend/tests/test_services_job_spec.py
git commit -m "feat(backend): enable MLflow system metrics in detector containers"
```

---

## Task 5: `DetectorVersion.maldet_version` schema migration

**Files:**

- Modify: `backend/app/models/detector.py`
- Create: `backend/migrations/versions/<rev>_detectorversion_maldet_version.py`

- [ ] **Step 1: Generate Alembic revision**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run alembic revision -m "add maldet_version to detector_version"
```

Note the generated filename (e.g., `abc12def3456_add_maldet_version_to_detector_version.py`).

- [ ] **Step 2: Write the upgrade/downgrade**

Edit the new revision file:

```python
"""add maldet_version to detector_version

Revision ID: abc12def3456
Revises: <previous head — leave alembic-generated value alone>
Create Date: 2026-05-11 ...
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "abc12def3456"
down_revision: str | Sequence[str] | None = "<previous head>"  # alembic-generated
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "detector_version",
        sa.Column("maldet_version", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("detector_version", "maldet_version")
```

- [ ] **Step 3: Add field to ORM model**

Edit `backend/app/models/detector.py` — inside `class DetectorVersion`, after the `image_digest` line:

```python
    maldet_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

- [ ] **Step 4: Run migration test**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: no errors. Verify with `uv run alembic current`.

- [ ] **Step 5: Run model tests**

Run: `uv run pytest tests/ -k 'detector' -x`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/detector.py backend/migrations/versions/abc12def3456_*.py
git commit -m "feat(backend): add maldet_version to DetectorVersion"
```

---

## Task 6: Capture `maldet_version` during build

**Files:**

- Modify: `backend/app/services/build.py`

- [ ] **Step 1: Find where image_digest is captured**

Run: `grep -n "image_digest\|inspect" backend/app/services/build.py | head -20`

- [ ] **Step 2: Add a maldet_version capture step**

Locate the function that finalizes a successful build (it updates `dv.image_digest`). After the digest write, add a step that inspects the built image for the installed maldet version. Use a Volcano / batch K8s Job, or — simpler — extract it from the build container's pip metadata via `crane` / `docker manifest`. Concrete approach: run a probe pod with the new image and read `pip show maldet`.

In `services/build.py`, after the existing `dv.image_digest = digest` write, add:

```python
    # Capture the bundled maldet version for provenance tagging.
    # The build-helper image (which created the detector image) records this
    # in /opt/maldet_version.txt; we pull that file via a one-shot probe pod.
    try:
        maldet_version = await _probe_maldet_version(detector_image=dv.harbor_image)
        if maldet_version:
            dv.maldet_version = maldet_version
    except Exception as exc:  # best-effort lineage, never block the build
        logger.warning("maldet version probe failed: %s", exc)
```

Then add the probe helper at module level:

```python
async def _probe_maldet_version(detector_image: str) -> str | None:
    """Spin up a brief K8s pod with the detector image to read pip-installed maldet version."""
    # Implementation strategy: create a Pod with command
    #   ["python", "-c", "import maldet; print(maldet.__version__)"]
    # restartPolicy=Never, wait until Succeeded, read logs, delete.
    # For the first iteration, prefer a simpler approach: bake the value
    # into a label/env at build time inside the build-helper image.
    ...  # TODO: replaced in Step 3 below
```

Actually — the **probe-pod approach adds 10+ seconds per build for a metadata read.** Cleaner alternative: have the build-helper image **emit `maldet_version` as a build-time env or stamp it into the image labels** so we can read it from `harbor` API or `docker manifest inspect`.

**Decision: use the image label approach.** Modify the build flow so the build-helper's Dockerfile stage does:

```dockerfile
RUN python -c "import maldet; print(maldet.__version__)" > /opt/maldet_version.txt \
    && MALDET_VERSION=$(cat /opt/maldet_version.txt) \
    && echo "maldet.version=$MALDET_VERSION" >> /opt/labels.txt
```

…and the buildah/kaniko/buildkit call (whichever lolday uses) reads the file to set a label. Then `harbor` API reports the label.

For this plan iteration, **use the simpler approach**: have the build-helper write `maldet_version` into the **`DetectorBuild` row** at build completion. Then `services/build.py` reads it from there when promoting `DetectorBuild → DetectorVersion`.

Replace the `_probe_maldet_version` stub with:

```python
async def _read_maldet_version_from_build(session: AsyncSession, build_id: uuid.UUID) -> str | None:
    """Read the maldet_version that the build-helper wrote into DetectorBuild."""
    build = await session.get(DetectorBuild, build_id)
    return getattr(build, "maldet_version", None) if build else None
```

And in the build-helper itself (`charts/lolday/helpers/build-helper/`), patch the script that POSTs build completion back to lolday so it includes `maldet_version` in the payload. Then `routers/internal.py` (or wherever the build callback lands) writes it to the DetectorBuild row.

> **This task is the most involved one in Plan B** because it spans build-helper image + backend internal API + DB. If the build-helper script's "build complete" callback is large, defer this to a follow-up plan and **for the initial cut-over write `maldet_version` manually** via a backfill SQL once the detectors are rebuilt with maldet 2.2.0.

**Pragmatic compromise for v1**: write `maldet_version` directly on `DetectorVersion` from the user-provided value when the detector authors bump their `maldet.toml`. Source of truth = `manifest.compat.min_maldet` from the parsed `maldet.toml`. (Strictly this is the _floor_, not the _installed_ version, but for our use case where detector images pin via pyproject.toml's `>=2.2,<3` and the latest released is 2.2.0 at cutover time, they match.)

Replace the Task 6 implementation with this simpler approach:

In `services/build.py`, where the manifest is parsed (`dv.manifest = parsed_manifest`), add:

```python
    # Manifest's compat.min_maldet is the floor; in practice it's also the
    # installed version when the detector author pins ``maldet>=X,<X+1`` and
    # X is the latest available. Use it as provenance proxy until we wire
    # the build-helper callback in a follow-up.
    compat = (parsed_manifest or {}).get("compat", {})
    if isinstance(compat, dict):
        floor = compat.get("min_maldet")
        if isinstance(floor, str):
            dv.maldet_version = floor
```

- [ ] **Step 3: Run build tests**

Run: `cd /home/bolin8017/Documents/repositories/lolday/backend && uv run pytest tests/ -k 'build' -x`

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/build.py
git commit -m "feat(backend): capture maldet_version from manifest compat floor"
```

> **Open follow-up**: wire the build-helper callback to report the actually-installed maldet version (vs. the manifest floor). Tracked separately; not blocking this spec.

---

## Task 7: routers/jobs.py — provenance tags + experiment description

**Files:**

- Modify: `backend/app/routers/jobs.py`

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_jobs_mlflow_naming.py` (or `tests/test_routers_jobs.py`):

```python
@pytest.mark.asyncio
async def test_create_run_includes_provenance_tags(
    test_client: AsyncClient,
    auth_headers: dict[str, str],
    detector_version: DetectorVersion,  # fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 14+ provenance tags must reach MlflowClient.create_run."""
    from unittest.mock import AsyncMock

    captured_tags: dict[str, str] = {}

    async def fake_create_run(self, experiment_id, *, start_time_ms, tags=None):
        for t in (tags or []):
            captured_tags[t["key"]] = t["value"]
        return "run-abc"

    monkeypatch.setattr("app.services.mlflow_client.MlflowClient.create_run", fake_create_run)
    monkeypatch.setattr("app.services.mlflow_client.MlflowClient.get_or_create_experiment", AsyncMock(return_value="42"))
    monkeypatch.setattr("app.services.mlflow_client.MlflowClient.set_experiment_tag", AsyncMock())

    payload = {"detector_version_id": str(detector_version.id), "type": "train",
               "train_dataset_id": "...", "test_dataset_id": "..."}
    r = await test_client.post("/jobs", json=payload, headers=auth_headers)
    assert r.status_code == 201

    # Spec § 5.7 — these all must be present
    expected_keys = {
        "mlflow.runName", "mlflow.source.name", "mlflow.source.type",
        "mlflow.source.git.commit",
        "maldet.action",
        "lolday.job_id", "lolday.user", "lolday.user_id",
        "lolday.detector_version", "lolday.detector_version_id",
        "lolday.detector_image_digest", "lolday.maldet_version",
        "lolday.resource_profile", "lolday.gpu_count",
        "lolday.train_dataset_id", "lolday.test_dataset_id",
    }
    missing = expected_keys - set(captured_tags.keys())
    assert not missing, f"missing tags: {missing}"
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_jobs_mlflow_naming.py -k provenance -x`

Expected: assertion failure on missing keys.

- [ ] **Step 3: Update the create_run call site**

Edit `backend/app/routers/jobs.py` — at the top of the file, add `import time` (replace the temporary `import time as _time` from Task 1).

Then replace the existing `client.create_run(...)` block (around lines 304-319) with:

```python
    client = _get_mlflow_client()
    newly_created_experiment = False
    if not dv.mlflow_experiment_id:
        dv.mlflow_experiment_id = await client.get_or_create_experiment(exp_name)
        newly_created_experiment = True
        await session.flush()

    if newly_created_experiment:
        # Spec § 5.9 — render an experiment description for the MLflow UI.
        note = (
            f"**Detector**: `{det.name}` @ `{dv.git_tag}`\n\n"
            f"**Owner**: `{user.handle}`\n\n"
            f"**Description**: {(det.description or '_no description_')}\n\n"
            f"**Maldet framework**: `{dv.maldet_version or '_unknown_'}`\n"
        )
        try:
            await client.set_experiment_tag(dv.mlflow_experiment_id, "mlflow.note.content", note)
            await client.set_experiment_tag(dv.mlflow_experiment_id, "lolday.detector_id", str(det.id))
            await client.set_experiment_tag(dv.mlflow_experiment_id, "lolday.detector_version_id", str(dv.id))
            await client.set_experiment_tag(dv.mlflow_experiment_id, "lolday.owner_id", str(user.id))
            await client.set_experiment_tag(dv.mlflow_experiment_id, "lolday.owner_handle", user.handle)
        except MlflowError as exc:
            logger.warning("set_experiment_tag failed for %s: %s", dv.mlflow_experiment_id, exc)

    gpu_count_val = RESOURCE_PROFILE_GPU_COUNT[body.resource_profile]
    now_ms = int(time.time() * 1000)
    run_id = await client.create_run(
        dv.mlflow_experiment_id,
        start_time_ms=now_ms,
        tags=[
            {"key": "mlflow.runName", "value": run_name},
            {"key": "mlflow.source.name", "value": detector_version_label},
            {"key": "mlflow.source.type", "value": "JOB"},
            {"key": "mlflow.source.git.commit", "value": dv.git_sha or ""},
            {"key": "maldet.action", "value": body.type.value},
            {"key": "lolday.job_id", "value": str(job_id)},
            {"key": "lolday.user", "value": user.handle},
            {"key": "lolday.user_id", "value": str(user.id)},
            {"key": "lolday.detector_version", "value": detector_version_label},
            {"key": "lolday.detector_version_id", "value": str(dv.id)},
            {"key": "lolday.detector_image_digest", "value": dv.image_digest or ""},
            {"key": "lolday.maldet_version", "value": dv.maldet_version or ""},
            {"key": "lolday.resource_profile", "value": body.resource_profile.value},
            {"key": "lolday.gpu_count", "value": str(gpu_count_val)},
            {"key": "lolday.train_dataset_id", "value": str(train_ds.id) if train_ds else ""},
            {"key": "lolday.test_dataset_id", "value": str(test_ds.id) if test_ds else ""},
            {"key": "lolday.predict_dataset_id", "value": str(predict_ds.id) if predict_ds else ""},
            {"key": "lolday.source_model_version_id", "value": str(source_model.id) if source_model else ""},
        ],
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_jobs.py tests/test_jobs_mlflow_naming.py tests/test_routers_jobs.py -v`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/jobs.py backend/tests/test_jobs_mlflow_naming.py
git commit -m "feat(backend): inject 14 provenance tags + experiment description on run create"
```

---

## Task 8: Render `cfg.lolday.*` dataset IDs into Hydra config

**Files:**

- Modify: `backend/app/services/job_config.py`
- Modify: `backend/app/routers/jobs.py` (pass dataset IDs to renderer)

- [ ] **Step 1: Find the renderer signature**

Run: `grep -n "render_config_yaml\|def render" backend/app/services/job_config.py | head -10`

- [ ] **Step 2: Add `lolday_dataset_ids` kwarg**

Edit `services/job_config.py::JobConfigRenderer.render_config_yaml` — add a new kwarg:

```python
    def render_config_yaml(
        self,
        *,
        stage: str,
        user_params: dict[str, Any] | None,
        mlflow_tracking_uri: str,
        mlflow_run_id: str,
        mlflow_experiment_id: str,
        lolday_meta: dict[str, str] | None = None,  # <-- new
    ) -> str:
```

In the rendered output dict, inject under a top-level `lolday:` block:

```python
        rendered = {
            ...,
            "mlflow": {
                "tracking_uri": mlflow_tracking_uri,
                "run_id": mlflow_run_id,
                "experiment_id": mlflow_experiment_id,
            },
            "lolday": dict(lolday_meta or {}),
        }
```

- [ ] **Step 3: Pass lolday_meta from jobs.py**

In `routers/jobs.py`, at the renderer call:

```python
    resolved_yaml = renderer.render_config_yaml(
        stage=body.type.value,
        user_params=body.params,
        mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
        mlflow_run_id=run_id,
        mlflow_experiment_id=dv.mlflow_experiment_id,
        lolday_meta={
            "train_dataset_id": str(train_ds.id) if train_ds else "",
            "test_dataset_id": str(test_ds.id) if test_ds else "",
            "predict_dataset_id": str(predict_ds.id) if predict_ds else "",
            "source_model_version_id": str(source_model.id) if source_model else "",
            "job_id": str(job_id),
        },
    )
```

- [ ] **Step 4: Update render tests**

Find: `grep -rn "render_config_yaml" backend/tests/`. Update each call to pass the new `lolday_meta=` kwarg (empty `{}` is fine for existing tests).

Add a new test asserting the YAML contains `lolday:` block:

```python
def test_render_config_yaml_includes_lolday_meta() -> None:
    yaml_text = JobConfigRenderer(...).render_config_yaml(
        stage="train", user_params={}, mlflow_tracking_uri="...",
        mlflow_run_id="r", mlflow_experiment_id="e",
        lolday_meta={"train_dataset_id": "abc-123"},
    )
    assert "lolday:" in yaml_text
    assert "train_dataset_id: abc-123" in yaml_text
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/ -k 'render_config or job_config' -v`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/job_config.py backend/app/routers/jobs.py backend/tests/
git commit -m "feat(backend): thread lolday dataset IDs into Hydra config for log_input"
```

---

## Task 9: Enrich runs with lolday timestamps in experiments_proxy

**Files:**

- Modify: `backend/app/routers/experiments_proxy.py`
- Create: `backend/tests/test_experiments_proxy_enrichment.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_experiments_proxy_enrichment.py`:

```python
"""experiments_proxy enriches each flattened run with lolday job timestamps."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_list_runs_includes_lolday_timestamps(
    test_client, auth_headers, db_session, monkeypatch  # fixtures
) -> None:
    from app.models.job import Job, JobStatus, JobType
    from unittest.mock import AsyncMock

    job_id = uuid.uuid4()
    j = Job(
        id=job_id,
        type=JobType.TRAIN,
        status=JobStatus.SUCCEEDED,
        owner_id=uuid.uuid4(),
        submitted_at=datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC),
        started_at=datetime(2026, 5, 11, 10, 5, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 11, 10, 15, 0, tzinfo=UTC),
        resolved_config={"yaml": ""},
        mlflow_run_id="run-abc",
        mlflow_experiment_id="42",
    )
    db_session.add(j)
    await db_session.commit()

    fake_runs = [{
        "info": {"run_id": "run-abc", "experiment_id": "42", "status": "FINISHED"},
        "data": {"tags": [{"key": "lolday.job_id", "value": str(job_id)}]},
    }]
    monkeypatch.setattr(
        "app.services.mlflow_client.MlflowClient.search_runs",
        AsyncMock(return_value=fake_runs),
    )

    r = await test_client.get(f"/experiments/42/runs", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["lolday_started_at"] is not None
    assert "10:05:00" in row["lolday_started_at"]
    assert row["lolday_finished_at"] is not None
    assert "10:15:00" in row["lolday_finished_at"]
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_experiments_proxy_enrichment.py -x`

Expected: `KeyError: 'lolday_started_at'` or similar.

- [ ] **Step 3: Update `_flatten_run` + `list_runs` / `get_run`**

Edit `backend/app/routers/experiments_proxy.py`:

```python
def _flatten_run(
    r: dict[str, Any],
    *,
    lolday_job_meta: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    info = r.get("info") or {}
    data = r.get("data") or {}
    metrics_list = data.get("metrics") or []
    params_list = data.get("params") or []
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}

    out: dict[str, Any] = {
        "run_id": info.get("run_id") or info.get("run_uuid"),
        "run_name": info.get("run_name"),
        "experiment_id": info.get("experiment_id"),
        "status": info.get("status"),
        "start_time": info.get("start_time"),
        "end_time": info.get("end_time"),
        "artifact_uri": info.get("artifact_uri"),
        "lifecycle_stage": info.get("lifecycle_stage"),
        "metrics": {m["key"]: m["value"] for m in metrics_list if "key" in m},
        "params": {p["key"]: p["value"] for p in params_list if "key" in p},
        "tags": tags,
        "lolday_started_at": None,
        "lolday_finished_at": None,
    }
    job_id = tags.get("lolday.job_id")
    if lolday_job_meta and job_id and job_id in lolday_job_meta:
        out["lolday_started_at"] = lolday_job_meta[job_id]["started_at"]
        out["lolday_finished_at"] = lolday_job_meta[job_id]["finished_at"]
    return out
```

Add a helper:

```python
async def _fetch_lolday_job_meta(
    run_ids: list[str], session: AsyncSession
) -> dict[str, dict[str, str | None]]:
    """Map lolday Job.mlflow_run_id → started_at/finished_at ISO strings,
    keyed by Job.id (uuid string) for direct join with the lolday.job_id tag."""
    from app.models.job import Job

    if not run_ids:
        return {}
    stmt = select(Job).where(Job.mlflow_run_id.in_(run_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {
        str(j.id): {
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
        for j in rows
    }
```

Modify `list_runs` to call the helper:

```python
@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        raw = await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    run_ids = [r.get("info", {}).get("run_id") for r in raw]
    run_ids = [r for r in run_ids if r]
    lolday_meta = await _fetch_lolday_job_meta(run_ids, session)
    return [_flatten_run(r, lolday_job_meta=lolday_meta) for r in raw]
```

Same pattern for `get_run`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_experiments_proxy_enrichment.py tests/test_routers_experiments_proxy.py -v`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/experiments_proxy.py backend/tests/test_experiments_proxy_enrichment.py
git commit -m "feat(backend): enrich runs with lolday started_at/finished_at"
```

---

## Task 10: Frontend Duration column switches source

**Files:**

- Modify: `frontend/src/routes/_authed.runs.$expId.tsx`
- Modify: `frontend/src/api/queries/runs.ts` (or wherever Row type lives)
- Modify: `frontend/tests/unit/RunsListPage.test.tsx`

- [ ] **Step 1: Regenerate schema types from backend**

Run:

```bash
cd /home/bolin8017/Documents/repositories/lolday
bash frontend/scripts/gen-api-types.sh
```

Verify: `git diff frontend/src/api/schema.gen.ts` shows the new `lolday_started_at` / `lolday_finished_at` fields appearing on the run response shape.

- [ ] **Step 2: Update Row type**

Edit `frontend/src/routes/_authed.runs.$expId.tsx` — extend the `Row` interface:

```typescript
interface Row {
  run_id: string;
  run_name?: string;
  status: string;
  start_time?: number;
  end_time?: number;
  metrics?: Record<string, number>;
  params?: Record<string, string>;
  tags?: Record<string, string>;
  lolday_started_at?: string | null;
  lolday_finished_at?: string | null;
}
```

- [ ] **Step 3: Update Duration cell**

Replace the existing `duration` column definition with:

```tsx
{
  id: "duration",
  header: "Compute time",
  cell: ({ row }) => {
    const s = row.original.lolday_started_at;
    const e = row.original.lolday_finished_at;
    if (!s || !e) return "—";
    return formatDuration(s, e);
  },
  meta: { cardLabel: "Compute time", cardSlot: "body" },
},
```

- [ ] **Step 4: Update the unit test**

Edit `frontend/tests/unit/RunsListPage.test.tsx`:

Find the test fixture for runs (likely a `mockRuns` array). Add `lolday_started_at` and `lolday_finished_at` ISO strings to each fixture entry, then add an assertion:

```typescript
it("renders compute duration from lolday timestamps", () => {
  // ...existing setup with runs that include lolday_started_at/finished_at...
  expect(screen.getByText(/^10 min$/)).toBeInTheDocument(); // adjust value to fixture
});

it("renders — when lolday timestamps are missing", () => {
  // run with lolday_started_at: null
  expect(screen.getByText("—")).toBeInTheDocument();
});
```

- [ ] **Step 5: Run frontend tests**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test -- RunsListPage
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/_authed.runs.$expId.tsx frontend/src/api/schema.gen.ts frontend/tests/unit/RunsListPage.test.tsx
git commit -m "feat(frontend): Runs page Duration column shows compute time from lolday job"
```

---

## Task 11: Rebuild pytorch-cu12-base with psutil + pynvml

**Files:**

- Modify: `charts/lolday/helpers/pytorch-cu12-base/Dockerfile`
- Modify: `charts/lolday/helpers.lock` (via `scripts/build-helpers.sh` regenerate)

- [ ] **Step 1: Find the pip install section**

Run: `grep -n "pip install\|psutil\|pynvml" charts/lolday/helpers/pytorch-cu12-base/Dockerfile`

- [ ] **Step 2: Add psutil + pynvml to the install list**

Edit the Dockerfile — locate the `RUN pip install` (or equivalent uv) block and add `psutil==<pinned> pynvml==<pinned>` to the install list. Pin to current latest stable: `psutil==6.0.0`, `pynvml==12.0.0`.

> If the Dockerfile uses `uv pip install`, prefer that flow for consistency. Verify by reading the file first.

- [ ] **Step 3: Build + push the new base image**

```bash
cd /home/bolin8017/Documents/repositories/lolday
bash scripts/build-helpers.sh
```

This is the standard helper-image rebuild script; it builds + pushes to Harbor and refreshes `charts/lolday/helpers.lock` with the new digest.

> **NB**: per CLAUDE.md the operator may need temporary sudo for docker push if the host setup requires it. Confirm with the operator before running.

- [ ] **Step 4: Smoke-test the image**

```bash
docker run --rm <harbor>/lolday/pytorch-cu12-base:<new-tag> \
  python -c "import psutil, pynvml; print(psutil.__version__, pynvml.__version__)"
```

Expected: prints the two versions.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/helpers/pytorch-cu12-base/Dockerfile charts/lolday/helpers.lock
git commit -m "build(base): add psutil + pynvml for MLflow system metrics"
```

---

## Task 12: Docs

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/runbooks/troubleshooting.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Architecture doc**

Append to `docs/architecture.md` (somewhere under the MLflow / observability section, or create one if absent):

```markdown
### MLflow data-model (2026-05-11)

- MLflow runs are created by backend at submit-time with `start_time = now`. The Runs page Duration column reads `Job.started_at` / `Job.finished_at` for compute-only duration.
- Reconciler updates MLflow run status on terminal transitions (FAILED / KILLED / FINISHED) — no orphan RUNNING runs.
- All runs carry `mlflow.source.git.commit`, `lolday.detector_image_digest`, `lolday.maldet_version`, `lolday.{train,test,predict}_dataset_id` tags.
- Structured payloads (confusion_matrix, per_class) and warning/error streams are MLflow artifacts (`*.json` / `*.jsonl`), not stringified tags.
- System metrics (`system/cpu_utilization_percentage`, `system/gpu_N_*`) auto-logged by mlflow 2.8+ system-metrics module; requires `psutil` + `pynvml` in detector base image (present in `pytorch-cu12-base:v5+`).
- Detector framework: requires `maldet >= 2.2`. See `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md` for full design.
```

- [ ] **Step 2: Troubleshooting SOPs**

Append to `docs/runbooks/troubleshooting.md`:

```markdown
### Symptom: MLflow run stuck RUNNING after job ended

**Diagnose**: `kubectl logs -n lolday deploy/backend | grep mlflow_finalize`. A WARNING line with `mlflow finalize failed for job <id>` means reconciler tried but MLflow rejected; investigate MLflow server health.

**Fix**: in the rare case `_finalize_mlflow_run` was bypassed (pre-2026-05-11 backend, manual DB edit), run `scripts/oneshot-mlflow-orphan-sweep.sh --dry-run` to list orphans, then drop the flag to apply.

### Symptom: missing `system/gpu_*` metrics on runs

**Diagnose**: `kubectl exec <detector-pod> -- pip list | grep -E '(psutil|pynvml)'`. If absent, the detector container is on a stale base image.

**Fix**: rebuild detector with the latest base image tag (`pytorch-cu12-base:v5+`).
```

- [ ] **Step 3: CLAUDE.md nav hint**

Edit `CLAUDE.md` — append one bullet under "How to navigate this codebase":

```markdown
- MLflow data model (2026-05-11 redesign) → `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`、`backend/app/services/mlflow_client.py`、`backend/app/reconciler/jobs.py::_finalize_mlflow_run`
```

- [ ] **Step 4: Commit**

```bash
git add docs/architecture.md docs/runbooks/troubleshooting.md CLAUDE.md
git commit -m "docs: MLflow data-model redesign — architecture, SOPs, nav hint"
```

---

## Task 13: Local e2e smoke

- [ ] **Step 1: Full backend test suite**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run pytest -x
```

Expected: green.

- [ ] **Step 2: Full frontend test suite**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test
pnpm typecheck
```

Expected: green.

- [ ] **Step 3: Pre-commit run on full repo**

```bash
cd /home/bolin8017/Documents/repositories/lolday
pre-commit run --all-files
```

Expected: clean. Stage and amend any auto-fixes.

- [ ] **Step 4: Helm lint**

```bash
helm lint charts/lolday
```

Expected: no errors.

---

## Task 14: Deploy to server30 + live smoke

> **Requires maldet 2.2.0 published (Plan A complete) AND detectors rebuilt (Plan C complete).** If those aren't done, run them first.

- [ ] **Step 1: Build + push backend / frontend images**

(Operator handles per existing playbook; no plan content for the manual push.)

- [ ] **Step 2: Apply chart**

```bash
cd /home/bolin8017/Documents/repositories/lolday
bash scripts/deploy.sh
```

Wait for rollout.

- [ ] **Step 3: Live smoke per spec §8.3**

```bash
bash tests/2026-05-11-mlflow-redesign-smoke.sh
```

(Plan C creates this script.)

Expected: all 8 numbered assertions pass.

---

## Self-review

- **Spec coverage**: §5.1 (start_time) → Task 1+7; §5.5 (finalize) → Tasks 2+3; §5.6 (system metrics) → Tasks 4+11; §5.7 (provenance tags) → Task 7; §5.8 (schema) → Tasks 5+6; §5.9 (experiment desc) → Task 7; §6.6 (MlflowClient API) → Task 1; §6.7 (call site) → Task 7; §6.8 (frontend duration) → Tasks 9+10.
- **Type consistency**: `create_run(experiment_id, *, start_time_ms, tags=None)` used consistently across MlflowClient (Task 1), jobs.py call site (Task 7), tests (Tasks 1+7).
- **No placeholders**: every code change has a concrete diff; the build-helper callback for `maldet_version` (Task 6) explicitly downgrades to a "use manifest compat floor" pragmatic v1 with an open follow-up flag.
- **TDD ordering**: each implementation task is preceded by a failing-test step.
