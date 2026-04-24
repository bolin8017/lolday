# Phase 11b: Lolday Backend — Detector Contract v1 Rewrite

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the lolday backend's detector-platform contract to consume `maldet` v1's structured outputs: read the `io.maldet.manifest` OCI image label for pre-flight capability introspection, render Hydra YAML configs for detector containers, launch Volcano Jobs with an `event-tailer` sidecar that streams `events.jsonl` into a new `job_events` table, and expose the live event stream to the frontend via WebSocket.

**Architecture:** Backend pins `maldet ~= 1.0` and imports `DetectorManifest` directly for schema-driven validation. The sidecar is a new `tail_events.py` subcommand in the existing `job-helper` image (now `v3`). Event delivery is in-process broadcast — the backend keeps an `asyncio.Queue` per active job (single-replica deployment; no Redis pub/sub needed). Job status determination switches from "Volcano Job phase" to "`stage_end.status` event", with the Volcano phase as a defensive fallback. The maldet `pyproject.toml` bug fixes from Phase 11a publish (wheel duplicate) are already in place; 11b just consumes the PyPI package.

**Tech Stack:** FastAPI (WebSocket + Depends-scoped auth), SQLAlchemy async, Alembic, kubernetes Python client, Volcano CRD `batch.volcano.sh/v1alpha1`, Pydantic v2 (imported from `maldet.manifest`), httpx (sidecar POST). Frontend: Recharts + React Query WebSocket.

**Spec:** `docs/superpowers/specs/2026-04-24-phase11-detector-framework-v1-design.md` (§3F "lolday Backend Changes" is the authoritative scope table).

**Depends on:** Phase 11a (maldet 1.0.0 live on PyPI) — complete.

**Blocks:** Phase 11c (template rewrites need the new manifest format accepted by lolday), Phase 11d (E2E).

**Server:** server30 (K3s v1.34.6+k3s1, 2× RTX 2080 Ti, port 9453 SSH).

**Constraints:**

- `bolin8017` has no persistent sudo; give sudo commands to the user.
- SSH on 9453 must survive every step (Cilium memory still applies).
- Work in a dedicated git worktree on branch `phase-11b-impl`; squash-merge to main per the established phase pattern.
- No backward compat with v0 detectors — the system has no production workload to preserve, and `islab-malware-detector` v0 will be retired in Phase 11d.
- Single backend replica: in-process WebSocket broadcasting is acceptable.
- Alembic is the schema source of truth (Phase 7.5 onward). Raw `ALTER TABLE` in `deploy.sh` is forbidden.
- `maldet ~= 1.0` pin: don't track unreleased features.

---

## File Structure

Backend changes:

```
backend/
├── pyproject.toml                       # MODIFY: add maldet>=1.0
├── uv.lock                              # MODIFY: regenerated
├── migrations/versions/
│   └── xxxx_phase11b_events_manifest.py # NEW: job_events table + detector_version.manifest column
├── app/
│   ├── config.py                        # MODIFY: +JOB_EVENT_FLUSH_BATCH_SIZE, +SIDECAR env vars
│   ├── reconciler.py                    # MODIFY: status determination from job_events
│   ├── models/
│   │   ├── __init__.py                  # MODIFY: export JobEvent
│   │   ├── job_event.py                 # NEW
│   │   └── detector.py                  # MODIFY: DetectorVersion.manifest JSONB column
│   ├── schemas/
│   │   ├── __init__.py                  # MODIFY
│   │   └── job_event.py                 # NEW
│   ├── services/
│   │   ├── harbor.py                    # MODIFY: + get_image_labels()
│   │   ├── validator.py                 # NEW: manifest + job pre-flight
│   │   ├── job_config.py                # MODIFY: rewrite as Hydra YAML renderer
│   │   ├── job_spec.py                  # MODIFY: maldet run + sidecar
│   │   ├── events_tail.py               # NEW: persistence + broadcast
│   │   └── manifest_store.py            # NEW: helper to decode OCI label → DetectorManifest
│   └── routers/
│       ├── internal.py                  # MODIFY: +POST /internal/jobs/{id}/events
│       └── jobs.py                      # MODIFY: +GET /jobs/{id}/events + WS
└── tests/
    ├── conftest.py                      # MODIFY: stub Harbor label fetch + maldet manifest fixture
    ├── fixtures/
    │   └── valid_maldet_manifest.json   # NEW: decoded manifest sample
    ├── test_services_manifest_store.py  # NEW
    ├── test_services_harbor_labels.py   # NEW
    ├── test_services_validator_phase11b.py # NEW
    ├── test_services_job_config_phase11b.py # NEW (replaces prior job_config test)
    ├── test_services_job_spec_phase11b.py   # NEW (replaces prior job_spec test)
    ├── test_services_events_tail.py     # NEW
    ├── test_models_job_event.py         # NEW
    ├── test_internal_events.py          # NEW
    ├── test_jobs_events_endpoint.py     # NEW (HTTP GET)
    └── test_jobs_events_websocket.py    # NEW (WS)
```

Helm chart changes:

```
charts/lolday/
├── Chart.yaml                            # MODIFY: 0.12.1 → 0.13.0
├── values.yaml                           # MODIFY: jobHelperImage v2 → v3, +EVENT_TAILER settings
├── templates/
│   ├── backend.yaml                      # MODIFY: new env INTERNAL_EVENTS_URL
│   └── backend-rbac.yaml                 # no change expected
└── helpers/job-helper/
    ├── Dockerfile                        # MODIFY: add curl (for healthcheck) — optional
    ├── pyproject.toml                    # MODIFY: +httpx dep for sidecar
    └── job_helper/
        └── tail_events.py                # NEW
```

Frontend changes:

```
frontend/src/
├── pages/
│   └── JobDetail.tsx                    # MODIFY: + live metric chart + event log
├── hooks/
│   └── useJobEvents.ts                  # NEW: WebSocket subscription
└── components/
    └── JobMetricChart.tsx               # NEW: Recharts wrapper
```

Scripts + docs:

```
scripts/
└── deploy.sh                            # MODIFY: backend image → phase11b, jobHelper v2 → v3

docs/
└── phase11b-e2e-checklist.md            # NEW
```

---

## Prerequisites

- Git worktree created on branch `phase-11b-impl`:
  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git worktree add ../lolday-phase11b phase-11b-impl
  cd ../lolday-phase11b
  ```
- Backend venv re-synced with `maldet`:
  ```bash
  cd backend && uv sync --group dev
  ```
- A real `maldet.toml`-backed detector image available for E2E (Task 22) — scaffold one locally via `maldet scaffold --template rf --name smoketest` and push to Harbor for the E2E phase. Can defer until Task 22.
- PyPI `maldet 1.0.0` reachable (Phase 11a complete).

---

## Branch + PR Workflow

- All commits go on `phase-11b-impl`. No direct commits to `main`.
- Each task is one focused commit (TDD cycle).
- Final PR against `main`, squash-merged. Commit message body lists the tasks as bullet points — this is how Phase 3/4/5/6 PRs were structured.

---

## Task 1: Add `maldet` to backend deps

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`

- [ ] **Step 1: Edit `backend/pyproject.toml`**

Add to `dependencies` list (keep alphabetical where possible; pandas/numpy already come transitively, so just add maldet):

```toml
    "maldet~=1.0",
```

Place it after `"kubernetes>=31.0.0",` alphabetically.

- [ ] **Step 2: Regenerate lock**

```bash
cd backend
uv lock
```

Expected: `uv.lock` updated with `maldet 1.0.0` and its deps (pydantic, hydra-core, etc. — some already present).

- [ ] **Step 3: Verify import works**

```bash
cd backend
uv run python -c "from maldet.manifest import DetectorManifest; print(DetectorManifest.__name__)"
```

Expected: `DetectorManifest`.

- [ ] **Step 4: Commit**

```bash
cd /path/to/lolday-phase11b
git add backend/pyproject.toml backend/uv.lock
git commit -m "deps: pin maldet~=1.0 (phase 11b bootstrap)"
```

---

## Task 2: Alembic migration — `job_events` table + `detector_version.manifest` column

**Files:**
- Create: `backend/migrations/versions/XXXX_phase11b_events_manifest.py`
- Modify: `backend/app/models/detector.py` (add column, so `alembic revision --autogenerate` picks it up)
- Test: `backend/tests/test_migrations_parity.py` (existing — run after migration)

- [ ] **Step 1: Add `manifest` column to the ORM**

Edit `backend/app/models/detector.py`, add to `DetectorVersion` class (find it in the file; the class has existing JSON columns you can pattern-match):

```python
    manifest: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
```

Place it next to other JSONB columns.

- [ ] **Step 2: Generate migration via autogenerate**

```bash
cd backend
uv run alembic revision --autogenerate -m "phase 11b events + manifest"
```

Inspect the generated file at `backend/migrations/versions/<hash>_phase_11b_events_manifest.py` — it should include the `detector_version.manifest` column addition. Autogenerate won't create the full `job_events` table because that table has no ORM model yet; we'll add the `create_table` manually in Step 3.

- [ ] **Step 3: Add `job_events` table to the migration**

Edit the generated file. Add to `upgrade()`:

```python
    op.create_table(
        "job_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_job_events_job_ts",
        "job_events",
        ["job_id", "ts"],
    )
```

Add to `downgrade()` (reverse order):

```python
    op.drop_index("ix_job_events_job_ts", table_name="job_events")
    op.drop_table("job_events")
```

- [ ] **Step 4: Apply migration locally (SQLite test DB)**

```bash
cd backend
uv run alembic upgrade head
```

Expected: no errors. Then run the migrations-parity test:

```bash
uv run pytest tests/test_migrations_parity.py -v
```

Note: this will fail until Task 3 adds the ORM model for `JobEvent` (autogenerate compares ORM → DB). That's fine — we run it again after Task 3.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/ backend/app/models/detector.py
git commit -m "feat(db): phase 11b migration — job_events + detector_version.manifest"
```

---

## Task 3: `JobEvent` ORM model

**Files:**
- Create: `backend/app/models/job_event.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_models_job_event.py`

- [ ] **Step 1: Write the failing test `tests/test_models_job_event.py`**

```python
"""JobEvent ORM insert/select round-trip."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detector, DetectorVersion, Job, JobEvent, User


@pytest.mark.asyncio
async def test_insert_and_query(async_session: AsyncSession) -> None:
    # Minimal fixtures
    user = User(id=uuid.uuid4(), email="t@example.com", hashed_password="x", is_active=True, is_verified=True)
    det = Detector(name="d1", display_name="d1", owner_id=user.id, git_url="https://example.com/r.git")
    dv = DetectorVersion(detector_id=det.id, tag="v1", image_digest="sha256:abc", manifest={})
    job = Job(type="train", status="pending", owner_id=user.id, detector_version_id=dv.id)

    async_session.add_all([user, det, dv, job])
    await async_session.commit()

    ev = JobEvent(
        job_id=job.id,
        ts=datetime(2026, 4, 24, tzinfo=timezone.utc),
        kind="stage_begin",
        payload={"stage": "train"},
    )
    async_session.add(ev)
    await async_session.commit()

    from sqlalchemy import select

    result = await async_session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.ts)
    )
    rows = list(result)
    assert len(rows) == 1
    assert rows[0].kind == "stage_begin"
    assert rows[0].payload == {"stage": "train"}
```

- [ ] **Step 2: Run — verify fail**

```bash
cd backend
uv run pytest tests/test_models_job_event.py -v
```

Expected: `ImportError: cannot import name 'JobEvent'`.

- [ ] **Step 3: Implement `app/models/job_event.py`**

```python
"""JobEvent ORM: structured events streamed from detector containers via sidecar."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

_JSONB = JSONB().with_variant(JSON(), "sqlite")


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_ts", "job_id", "ts"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Export from `app/models/__init__.py`**

Find the existing export line (models __init__ collects the exports). Add `JobEvent`:

```python
from app.models.job_event import JobEvent

__all__ = [..., "JobEvent"]  # merge into existing __all__
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/test_models_job_event.py -v
uv run pytest tests/test_migrations_parity.py -v
```

Both must pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/job_event.py backend/app/models/__init__.py backend/tests/test_models_job_event.py
git commit -m "feat(models): JobEvent ORM (phase 11b event stream persistence)"
```

---

## Task 4: Harbor — read image labels

**Files:**
- Modify: `backend/app/services/harbor.py` (add `get_image_labels`)
- Test: `backend/tests/test_services_harbor_labels.py`

- [ ] **Step 1: Write the failing test**

```python
"""HarborClient.get_image_labels: decode OCI image config Labels field."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.harbor import HarborClient


@pytest.mark.asyncio
@respx.mock
async def test_get_image_labels_returns_dict() -> None:
    # Harbor returns {"extra_attrs": {"config": {"Labels": {"io.maldet.manifest": "..."}}}}
    respx.get(
        "http://harbor.example/api/v2.0/projects/detectors/repositories/r1/artifacts/sha256:abc"
    ).mock(
        return_value=Response(
            200,
            json={
                "digest": "sha256:abc",
                "extra_attrs": {
                    "config": {
                        "Labels": {
                            "io.maldet.manifest": "eyJzY2hlbWFfdmVyc2lvbiI6IDF9",
                            "io.maldet.framework": "sklearn",
                            "org.opencontainers.image.version": "2.0.0",
                        }
                    }
                },
            },
        )
    )

    client = HarborClient("http://harbor.example", "u", "p")
    labels = await client.get_image_labels("detectors", "r1", "sha256:abc")
    assert labels["io.maldet.framework"] == "sklearn"
    assert labels["io.maldet.manifest"] == "eyJzY2hlbWFfdmVyc2lvbiI6IDF9"


@pytest.mark.asyncio
@respx.mock
async def test_get_image_labels_empty_if_no_config() -> None:
    respx.get(
        "http://harbor.example/api/v2.0/projects/detectors/repositories/r1/artifacts/sha256:def"
    ).mock(return_value=Response(200, json={"digest": "sha256:def"}))

    client = HarborClient("http://harbor.example", "u", "p")
    labels = await client.get_image_labels("detectors", "r1", "sha256:def")
    assert labels == {}
```

- [ ] **Step 2: Run — fail expected**

```bash
uv run pytest tests/test_services_harbor_labels.py -v
```

- [ ] **Step 3: Implement `get_image_labels` in `app/services/harbor.py`**

Find the `class HarborClient` block and add this method (adjacent to existing `get_scan`):

```python
    async def get_image_labels(
        self, project: str, repository: str, digest: str
    ) -> dict[str, str]:
        """Return OCI image config Labels dict (may be empty).

        Harbor exposes these via ``/api/v2.0/projects/.../artifacts/<digest>`` at
        ``extra_attrs.config.Labels``.
        """
        url = f"/api/v2.0/projects/{project}/repositories/{repository}/artifacts/{digest}"
        async with self._client() as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.json()
        config = ((data.get("extra_attrs") or {}).get("config") or {})
        labels = config.get("Labels") or {}
        return dict(labels)
```

- [ ] **Step 4: Run — pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/harbor.py backend/tests/test_services_harbor_labels.py
git commit -m "feat(harbor): HarborClient.get_image_labels reads OCI image config Labels"
```

---

## Task 5: `manifest_store` — decode OCI label → DetectorManifest

**Files:**
- Create: `backend/app/services/manifest_store.py`
- Create: `backend/tests/fixtures/valid_maldet_manifest.json`
- Test: `backend/tests/test_services_manifest_store.py`

- [ ] **Step 1: Create the fixture**

```bash
mkdir -p backend/tests/fixtures
```

Write `backend/tests/fixtures/valid_maldet_manifest.json`:

```json
{
  "detector": {"name": "elfrfdet", "version": "2.0.0", "framework": "sklearn"},
  "input": {"binary_format": "elf", "required_sections": [".text"], "dataset_contract": "sample_csv"},
  "output": {"task": "binary_classification", "classes": ["Malware", "Benign"], "score_range": [0.0, 1.0]},
  "resources": {"supports": ["cpu", "gpu1", "gpu2"], "recommended": "cpu", "min_memory_gib": 2, "gpu_required": false},
  "lifecycle": {"stages": ["train", "evaluate", "predict"], "supports_serving": false, "supports_hpsweep": true, "supports_distributed": false, "supports_multinode": false},
  "artifacts": {
    "model": {"path": "model/", "type": "dir"},
    "metrics": {"path": "metrics.json", "type": "file"},
    "predictions": {"path": "predictions.csv", "type": "file"}
  },
  "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
  "stages": {
    "train": {
      "reader": "maldet.builtins.readers:SampleCsvReader",
      "extractor": "elfrfdet.features:Text256Extractor",
      "model": "elfrfdet.models:make_rf",
      "trainer": "maldet.trainers.sklearn_trainer:SklearnTrainer",
      "evaluator": "maldet.evaluators.binary:BinaryClassification"
    },
    "evaluate": {
      "reader": "maldet.builtins.readers:SampleCsvReader",
      "extractor": "elfrfdet.features:Text256Extractor",
      "evaluator": "maldet.evaluators.binary:BinaryClassification"
    },
    "predict": {
      "reader": "maldet.builtins.readers:SampleCsvReader",
      "extractor": "elfrfdet.features:Text256Extractor",
      "predictor": "maldet.builtins.predictors:BatchPredictor"
    }
  }
}
```

- [ ] **Step 2: Write failing test `tests/test_services_manifest_store.py`**

```python
"""manifest_store: decode base64 OCI label → DetectorManifest."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.services.manifest_store import (
    ManifestDecodeError,
    decode_manifest_label,
)

FIX = Path(__file__).parent / "fixtures" / "valid_maldet_manifest.json"


def _b64(j: dict) -> str:
    return base64.b64encode(json.dumps(j).encode("utf-8")).decode("ascii")


def test_decode_valid_label() -> None:
    raw = json.loads(FIX.read_text())
    label = _b64(raw)
    manifest = decode_manifest_label(label)
    assert manifest.detector.name == "elfrfdet"
    assert manifest.resources.supports == ["cpu", "gpu1", "gpu2"]


def test_decode_malformed_base64_raises() -> None:
    with pytest.raises(ManifestDecodeError, match="base64"):
        decode_manifest_label("@@@ not base64 @@@")


def test_decode_invalid_json_raises() -> None:
    bad = base64.b64encode(b"not json").decode("ascii")
    with pytest.raises(ManifestDecodeError, match="json"):
        decode_manifest_label(bad)


def test_decode_pydantic_failure_raises() -> None:
    bad_shape = _b64({"detector": {"name": ""}})  # missing required fields
    with pytest.raises(ManifestDecodeError, match="manifest"):
        decode_manifest_label(bad_shape)
```

- [ ] **Step 3: Implement `app/services/manifest_store.py`**

```python
"""Decode and persist the OCI-label-embedded maldet DetectorManifest."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from maldet.manifest import DetectorManifest


class ManifestDecodeError(ValueError):
    """Raised when an ``io.maldet.manifest`` label cannot be decoded."""


def decode_manifest_label(label_value: str) -> DetectorManifest:
    """Return the DetectorManifest encoded in a base64 JSON OCI label.

    Raises :class:`ManifestDecodeError` on base64 / JSON / schema failure.
    """
    try:
        raw = base64.b64decode(label_value, validate=True)
    except binascii.Error as exc:
        raise ManifestDecodeError(f"invalid base64: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestDecodeError(f"invalid json: {exc}") from exc
    try:
        return DetectorManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ManifestDecodeError(f"manifest schema validation failed: {exc}") from exc
```

- [ ] **Step 4: Pass + commit**

```bash
uv run pytest tests/test_services_manifest_store.py -v
git add backend/app/services/manifest_store.py backend/tests/test_services_manifest_store.py backend/tests/fixtures/valid_maldet_manifest.json
git commit -m "feat(services): manifest_store decodes io.maldet.manifest OCI label"
```

---

## Task 5b: Populate `detector_version.manifest` during build reconciliation

**Files:**
- Modify: `backend/app/reconciler.py` (inside `reconcile_build` — find the success path where image digest is recorded)
- Test: extend an existing reconciler-build test to assert the manifest is stored, OR add a focused test.

- [ ] **Step 1: Locate the success path in `reconcile_build`**

Search `backend/app/reconciler.py` for where `DetectorBuildStatus.SUCCEEDED` is set and the `detector_version` row is created. That's where the manifest fetch + decode fits — after the image is in Harbor and before the version row is finalized.

- [ ] **Step 2: Add the manifest fetch**

Insert after the image digest is known but before the row is committed:

```python
from app.services.manifest_store import ManifestDecodeError, decode_manifest_label

# ... inside reconcile_build after Harbor scan Success ...
labels = await harbor.get_image_labels(
    project=settings.HARBOR_DETECTORS_PROJECT,
    repository=build.image_repository,
    digest=image_digest,
)
manifest_label = labels.get("io.maldet.manifest")
if not manifest_label:
    build.status = DetectorBuildStatus.FAILED
    build.failure_reason = "manifest_label_missing"
    BACKEND_ERRORS.labels(stage="manifest_missing").inc()
    logger.exception("build image has no io.maldet.manifest label", extra={"build_id": str(build.id)})
    await session.commit()
    return

try:
    manifest = decode_manifest_label(manifest_label)
except ManifestDecodeError as exc:
    build.status = DetectorBuildStatus.FAILED
    build.failure_reason = "manifest_invalid"
    BACKEND_ERRORS.labels(stage="manifest_invalid").inc()
    logger.exception("manifest decode failed", extra={"build_id": str(build.id), "err": str(exc)})
    await session.commit()
    return

# ... existing DetectorVersion(...) construction — pass manifest=manifest.model_dump(mode="json") ...
```

Update the `DetectorVersion(...)` constructor call already in that function to include `manifest=manifest.model_dump(mode="json")`.

- [ ] **Step 3: Write focused test**

Create `backend/tests/test_reconciler_manifest.py`:

```python
"""reconcile_build populates DetectorVersion.manifest from Harbor label."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

FIX = Path(__file__).parent / "fixtures" / "valid_maldet_manifest.json"


@pytest.mark.asyncio
async def test_reconcile_build_persists_manifest(async_session, _scan_ok_image, seed_build_scanning):
    """Given a build whose scan is Success and image has a valid maldet manifest
    label, reconcile_build must populate the new DetectorVersion.manifest column.
    """
    # Expect the concrete test infrastructure (_scan_ok_image, seed_build_scanning)
    # to be added as conftest fixtures in this task. If they don't exist yet, define
    # them here using existing test helpers. Adjust imports/fixture names to match
    # whatever conftest.py exposes.
    from app.models.detector import DetectorVersion
    from app.reconciler import reconcile_build

    build = await seed_build_scanning()  # returns DetectorBuild row
    manifest_json = json.loads(FIX.read_text())
    label = base64.b64encode(json.dumps(manifest_json).encode()).decode("ascii")

    with (
        patch("app.reconciler.harbor.get_scan", new=AsyncMock(return_value=_scan_ok_image)),
        patch("app.reconciler.harbor.get_image_labels", new=AsyncMock(return_value={"io.maldet.manifest": label})),
    ):
        await reconcile_build(async_session, build)

    # Fetch the just-created DetectorVersion row
    from sqlalchemy import select
    dv = (await async_session.scalars(select(DetectorVersion).where(DetectorVersion.detector_id == build.detector_id))).first()
    assert dv is not None
    assert dv.manifest["detector"]["name"] == "elfrfdet"
```

Note: `_scan_ok_image` and `seed_build_scanning` fixtures may need to be defined in this task's conftest or the test adapted to match existing reconciler tests' setup. If existing reconciler tests in `test_reconciler_jobs.py` use a different pattern, follow that pattern.

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/test_reconciler_manifest.py -v
uv run pytest tests/test_reconciler_jobs.py -v  # regression
git add backend/app/reconciler.py backend/tests/test_reconciler_manifest.py
git commit -m "feat(reconciler): persist io.maldet.manifest into DetectorVersion.manifest"
```

---

## Task 6: Validator — manifest + job pre-flight

**Files:**
- Create: `backend/app/services/validator.py` (new OR extend existing `services/validator.py` — the Phase 4 codebase may already have a stub; check first)
- Test: `backend/tests/test_services_validator_phase11b.py`

- [ ] **Step 1: Check if `app/services/validator.py` already exists**

```bash
ls backend/app/services/validator.py 2>&1
```

If it exists, read it first. Phase 3 had a `validator.py` for detector schema validation during builds. Phase 11b adds **job-submission pre-flight** based on the manifest. Integrate into the existing file if present; create new if not.

- [ ] **Step 2: Write the failing test**

```python
"""Phase 11b job-submission pre-flight: manifest + dataset + resource compatibility."""

from __future__ import annotations

import pytest

from app.models.job import ResourceProfile
from app.services.validator import (
    JobSubmissionError,
    validate_job_submission,
)
from maldet.manifest import DetectorManifest


def _manifest(**overrides) -> DetectorManifest:
    data = {
        "detector": {"name": "d", "version": "1", "framework": "sklearn"},
        "input": {"binary_format": "elf", "required_sections": [], "dataset_contract": "sample_csv"},
        "output": {"task": "binary_classification", "classes": ["Malware", "Benign"], "score_range": [0.0, 1.0]},
        "resources": {"supports": ["cpu"], "recommended": "cpu", "min_memory_gib": 1, "gpu_required": False},
        "lifecycle": {"stages": ["train", "evaluate", "predict"], "supports_serving": False, "supports_hpsweep": True, "supports_distributed": False, "supports_multinode": False},
        "artifacts": {
            "model": {"path": "model/", "type": "dir"},
            "metrics": {"path": "metrics.json", "type": "file"},
            "predictions": {"path": "predictions.csv", "type": "file"},
        },
        "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
        "stages": {},
    }
    data.update(overrides)
    return DetectorManifest.model_validate(data)


def test_accepts_supported_profile() -> None:
    m = _manifest()
    validate_job_submission(manifest=m, resource_profile=ResourceProfile.STANDARD, dataset_contract="sample_csv", stage="train")


def test_rejects_unsupported_profile() -> None:
    m = _manifest()  # supports = ["cpu"]
    with pytest.raises(JobSubmissionError, match="resource_profile"):
        validate_job_submission(manifest=m, resource_profile=ResourceProfile.GPU2, dataset_contract="sample_csv", stage="train")


def test_rejects_mismatched_dataset_contract() -> None:
    m = _manifest()  # contract = sample_csv
    with pytest.raises(JobSubmissionError, match="dataset_contract"):
        validate_job_submission(manifest=m, resource_profile=ResourceProfile.STANDARD, dataset_contract="sample_jsonl", stage="train")


def test_rejects_stage_not_declared() -> None:
    m = _manifest(lifecycle={
        "stages": ["train", "evaluate"],  # no predict
        "supports_serving": False, "supports_hpsweep": True,
        "supports_distributed": False, "supports_multinode": False,
    })
    with pytest.raises(JobSubmissionError, match="stage"):
        validate_job_submission(manifest=m, resource_profile=ResourceProfile.STANDARD, dataset_contract="sample_csv", stage="predict")
```

- [ ] **Step 3: Implement `app/services/validator.py`**

If `validator.py` already exists with Phase 3 content, append these at the end; otherwise create new file with only these:

```python
"""Phase 11b: manifest + job-submission pre-flight validators."""

from __future__ import annotations

from maldet.manifest import DetectorManifest

from app.models.job import ResourceProfile


_PROFILE_TO_MANIFEST_TOKEN = {
    ResourceProfile.STANDARD: "cpu",
    ResourceProfile.GPU1: "gpu1",
    ResourceProfile.GPU2: "gpu2",
}

# Platform accepts these dataset contracts.
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
    """Pre-flight checks that can only be done once both the detector manifest
    and the incoming job submission are known."""

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
```

- [ ] **Step 4: Wire into `routers/jobs.py` POST /jobs**

Find where new jobs are created. Before writing the Job row, call `validate_job_submission`. On `JobSubmissionError`, return HTTP 400 with the message.

```python
from app.services.validator import JobSubmissionError, validate_job_submission
from app.services.manifest_store import ManifestDecodeError
# ... in the POST /jobs handler ...
dv = await session.get(DetectorVersion, job_in.detector_version_id)
if dv is None or dv.manifest is None:
    raise HTTPException(status_code=400, detail="detector_version has no maldet manifest")
try:
    manifest = DetectorManifest.model_validate(dv.manifest)
except Exception as exc:
    raise HTTPException(status_code=400, detail=f"stored manifest invalid: {exc}") from exc
try:
    validate_job_submission(
        manifest=manifest,
        resource_profile=job_in.resource_profile,
        dataset_contract="sample_csv",
        stage=job_in.type.value,
    )
except JobSubmissionError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 5: Run + commit**

```bash
uv run pytest tests/test_services_validator_phase11b.py -v
uv run pytest tests/test_jobs.py -v  # regression
git add backend/app/services/validator.py backend/app/routers/jobs.py backend/tests/test_services_validator_phase11b.py
git commit -m "feat(validator): phase 11b manifest + job pre-flight (resource / dataset / stage)"
```

---

## Task 7: `job_config.py` — rewrite as Hydra YAML renderer

**Files:**
- Modify: `backend/app/services/job_config.py` (complete rewrite, preserve file shape)
- Replace: `backend/tests/test_services_job_config.py` content
- Create: `backend/tests/test_services_job_config_phase11b.py` (or fully replace the old one)

- [ ] **Step 1: Write the new test**

```python
"""JobConfigRenderer (phase 11b): Hydra YAML + CSV renderer."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from app.services.job_config import JobConfigRenderer


def test_render_train_yaml_shape() -> None:
    renderer = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = renderer.render_config_yaml(
        stage="train",
        user_params={"model": {"n_estimators": 500}},
        mlflow_tracking_uri="http://mlflow:5000",
        mlflow_run_id="r123",
        mlflow_experiment_id="e9",
    )
    doc = yaml.safe_load(cfg)
    assert doc["stage"] == "train"
    assert doc["paths"]["config_dir"] == "/mnt/config"
    assert doc["paths"]["samples_root"] == "/mnt/samples"
    assert doc["paths"]["output_dir"] == "/mnt/output"
    # user params merged into `model`
    assert doc["model"]["n_estimators"] == 500
    # mlflow block populated from args
    assert doc["mlflow"]["tracking_uri"] == "http://mlflow:5000"
    assert doc["mlflow"]["run_id"] == "r123"


def test_render_evaluate_uses_source_model_path() -> None:
    renderer = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = renderer.render_config_yaml(
        stage="evaluate",
        user_params={},
        mlflow_tracking_uri="",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    doc = yaml.safe_load(cfg)
    assert doc["stage"] == "evaluate"
    assert doc["paths"]["source_model"] == "/mnt/source-model"


def test_train_csv_content_is_stored_as_string() -> None:
    """CSVs are passed separately — renderer does NOT embed them in YAML."""
    renderer = JobConfigRenderer("/s", "/c", "/o", "/m")
    assert hasattr(renderer, "render_csv_files")  # separate method


def test_render_csv_files_returns_dict_of_named_files() -> None:
    renderer = JobConfigRenderer("/s", "/c", "/o", "/m")
    files = renderer.render_csv_files(
        train_csv="file_name,label\nabc,Malware\n",
        test_csv=None,
        predict_csv=None,
    )
    assert files == {"train.csv": "file_name,label\nabc,Malware\n"}


def test_overrides_flatten_nested_params() -> None:
    """User-facing param overrides can come flat (`model.n_estimators=500`) — renderer
    turns them into nested dict before emission."""
    renderer = JobConfigRenderer("/s", "/c", "/o", "/m")
    cfg = renderer.render_config_yaml(
        stage="train",
        user_params={"model.n_estimators": 500, "trainer.n_jobs": 4},
        mlflow_tracking_uri="",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    doc = yaml.safe_load(cfg)
    assert doc["model"]["n_estimators"] == 500
    assert doc["trainer"]["n_jobs"] == 4
```

- [ ] **Step 2: Rewrite `app/services/job_config.py`**

```python
"""Phase 11b: render Hydra YAML config + separate CSV files for the detector container.

Replaces the Phase 4 JSON renderer. The detector reads config.yaml; the CSVs
are written to the config mount as side-files.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import yaml


def compute_idempotency_key(
    *,
    user_id: str,
    detector_version_id: str,
    job_type: str,
    train_ds: str | None,
    test_ds: str | None,
    predict_ds: str | None,
    source_model: str | None,
    params: dict[str, Any],
) -> str:
    payload = {
        "user": user_id,
        "dv": detector_version_id,
        "type": job_type,
        "train_ds": train_ds,
        "test_ds": test_ds,
        "predict_ds": predict_ds,
        "source_model": source_model,
        "params": params,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k] = _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _unflatten(params: dict[str, Any]) -> dict[str, Any]:
    """Turn ``{"model.n_estimators": 500}`` into ``{"model": {"n_estimators": 500}}``."""
    out: dict[str, Any] = {}
    for raw_key, val in params.items():
        if "." not in raw_key:
            out[raw_key] = val
            continue
        parts = raw_key.split(".")
        cursor = out
        for p in parts[:-1]:
            if p not in cursor or not isinstance(cursor[p], dict):
                cursor[p] = {}
            cursor = cursor[p]
        cursor[parts[-1]] = val
    return out


@dataclass(frozen=True)
class JobConfigRenderer:
    samples_root: str
    config_mount: str
    output_mount: str
    source_model_mount: str

    def render_config_yaml(
        self,
        *,
        stage: str,
        user_params: dict[str, Any],
        mlflow_tracking_uri: str,
        mlflow_run_id: str | None,
        mlflow_experiment_id: str | None,
    ) -> str:
        base: dict[str, Any] = {
            "defaults": ["_self_"],
            "stage": stage,
            "paths": {
                "config_dir": self.config_mount,
                "output_dir": self.output_mount,
                "samples_root": self.samples_root,
                "source_model": self.source_model_mount,
            },
            "data": {
                "train_csv": f"{self.config_mount}/train.csv",
                "test_csv": f"{self.config_mount}/test.csv",
                "predict_csv": f"{self.config_mount}/predict.csv",
            },
            "mlflow": {
                "tracking_uri": mlflow_tracking_uri or None,
                "run_id": mlflow_run_id,
                "experiment_id": mlflow_experiment_id,
            },
        }
        nested = _unflatten(copy.deepcopy(user_params))
        merged = _deep_merge(base, nested)
        return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)

    def render_csv_files(
        self,
        *,
        train_csv: str | None,
        test_csv: str | None,
        predict_csv: str | None,
    ) -> dict[str, str]:
        """Return the non-null CSVs keyed by their target filename inside ``config_dir``."""
        out = {}
        if train_csv is not None:
            out["train.csv"] = train_csv
        if test_csv is not None:
            out["test.csv"] = test_csv
        if predict_csv is not None:
            out["predict.csv"] = predict_csv
        return out
```

- [ ] **Step 3: Update `routers/internal.py /jobs/{id}/config` to return the new shape**

Find the existing handler. It currently returns `JobInternalConfig(config=job.resolved_config, train_csv=..., ...)`. That's fine — `job.resolved_config` now stores the YAML string instead of a JSON dict. Look at the Job model (`backend/app/models/job.py`) — if `resolved_config` is typed as JSONB, change to `Text` via a subsequent Alembic migration, OR store the YAML **inside** a single `yaml_text` key (`{"yaml_text": "..."}`).

**Decision for v1**: add a new Alembic migration `Phase 11b.2 — job.resolved_config_yaml` adding a new `resolved_config_yaml TEXT` column, keep the old JSONB `resolved_config` for rollback compat (populated to `null` from 11b forward). Update `JobInternalConfig` schema to include `yaml: str` alongside legacy `config`.

Apply only if the combined migration in Task 2 doesn't already cover this. Check `job.resolved_config` column type — if it's already `JSONB` and you want to switch to TEXT, add the `resolved_config_yaml` column in Task 2's migration (retro-fit into the same migration rather than creating a 3rd).

- [ ] **Step 4: Commit**

```bash
uv run pytest tests/test_services_job_config_phase11b.py -v
git add backend/app/services/job_config.py backend/tests/test_services_job_config_phase11b.py
git rm backend/tests/test_services_job_config.py 2>/dev/null || true  # if the old file is obsolete
git commit -m "feat(job_config): phase 11b Hydra YAML renderer (replaces JSON)"
```

If retrofitting the Alembic migration: also `git add` the migration file.

---

## Task 8: `job_spec.py` — `maldet run` + sidecar event-tailer

**Files:**
- Modify: `backend/app/services/job_spec.py`
- Replace/create: `backend/tests/test_services_job_spec_phase11b.py`

- [ ] **Step 1: Write the failing test**

```python
"""job_spec (phase 11b): detector container runs `maldet run`; sidecar tails events."""

from __future__ import annotations

import uuid

from app.models.job import JobType, ResourceProfile
from app.services.job_spec import build_volcano_job_manifest


def _build() -> dict:
    return build_volcano_job_manifest(
        job_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        job_type=JobType.TRAIN,
        detector_image="harbor/lolday/elfrfdet:v2.0.0",
        mlflow_experiment_id="e1",
        mlflow_run_id="r1",
        mlflow_tracking_uri="http://mlflow:5000",
        source_run_id=None,
        source_artifact_path=None,
        resource_profile=ResourceProfile.STANDARD,
        internal_events_url="http://backend:8000/internal/jobs/12345678-1234-5678-1234-567812345678/events",
        job_token="test-token",
        gpu_strategy="ddp",
    )


def test_detector_command_is_maldet_run() -> None:
    m = _build()
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    assert container["name"] == "detector"
    assert container["command"] == ["maldet"]
    assert container["args"] == ["run", "train", "--config", "/mnt/config/config.yaml"]


def test_has_event_tailer_sidecar() -> None:
    m = _build()
    containers = m["spec"]["tasks"][0]["template"]["spec"]["containers"]
    assert len(containers) == 2
    sidecar = next(c for c in containers if c["name"] == "event-tailer")
    assert any(mount["name"] == "output" for mount in sidecar["volumeMounts"])


def test_sidecar_reads_internal_events_url_and_token() -> None:
    m = _build()
    sidecar = next(
        c for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "event-tailer"
    )
    env = {e["name"]: e for e in sidecar["env"]}
    assert env["INTERNAL_EVENTS_URL"]["value"].endswith("/events")
    assert env["JOB_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"


def test_detector_environment_injects_gpu_strategy() -> None:
    m = _build()
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MALDET_GPU_COUNT"] == "0"  # STANDARD => 0
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "ddp"
```

- [ ] **Step 2: Rewrite `app/services/job_spec.py`**

Replace `_detector_container` and `build_volcano_job_manifest` (keep `_config_writer_init` and `_model_fetcher_init`; they still work). Preserve the file's top-level structure but change:

1. `_detector_container` — new signature adds `gpu_count: int`, `gpu_strategy: str`. Command becomes `["maldet"]`, args become `["run", stage, "--config", "/mnt/config/config.yaml"]`. Add `MALDET_GPU_COUNT`, `MALDET_DISTRIBUTED_STRATEGY` env vars.
2. New `_event_tailer_sidecar(job_id, internal_events_url)` function — Python sidecar container running `python -m job_helper.tail_events /mnt/output/events.jsonl`.
3. `build_volcano_job_manifest` — new params `internal_events_url`, `job_token` (passed through to sidecar), `gpu_strategy`. Adds the sidecar to the task `containers` list.

Full code:

```python
from app.models.job import RESOURCE_PROFILE_GPU_COUNT, JobType, ResourceProfile
# ... keep existing helpers (POD_LABEL_NAME, job_name, _active_deadline, _job_token_secret_name, build_job_token_secret) ...


def _detector_container(
    detector_image: str,
    action: str,
    mlflow_tracking_uri: str,
    mlflow_run_id: str,
    mlflow_experiment_id: str,
    gpu_count: int,
    gpu_strategy: str,
) -> dict[str, Any]:
    return {
        "name": "detector",
        "image": detector_image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["maldet"],
        "args": ["run", action, "--config", "/mnt/config/config.yaml"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "MLFLOW_RUN_ID", "value": mlflow_run_id},
            {"name": "MLFLOW_EXPERIMENT_ID", "value": mlflow_experiment_id},
            {"name": "MALDET_MANIFEST", "value": "/app/maldet.toml"},
            {"name": "MALDET_GPU_COUNT", "value": str(gpu_count)},
            {"name": "MALDET_DISTRIBUTED_STRATEGY", "value": gpu_strategy},
            {"name": "TMPDIR", "value": "/tmp"},
            {"name": "HOME", "value": "/tmp"},
        ],
        "volumeMounts": [
            {"name": "config", "mountPath": "/mnt/config", "readOnly": True},
            {"name": "output", "mountPath": "/mnt/output"},
            {"name": "source-model", "mountPath": "/mnt/source-model", "readOnly": True},
            {"name": "samples", "mountPath": "/mnt/samples", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": gpu_count},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _event_tailer_sidecar(job_id: uuid.UUID, internal_events_url: str) -> dict[str, Any]:
    return {
        "name": "event-tailer",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.tail_events"],
        "args": ["/mnt/output/events.jsonl"],
        "env": [
            {"name": "INTERNAL_EVENTS_URL", "value": internal_events_url},
            {
                "name": "JOB_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": _job_token_secret_name(job_id),
                        "key": "token",
                    }
                },
            },
        ],
        "volumeMounts": [
            {"name": "output", "mountPath": "/mnt/output"},
        ],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def build_volcano_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    detector_image: str,
    mlflow_experiment_id: str,
    mlflow_run_id: str,
    mlflow_tracking_uri: str,
    source_run_id: str | None,
    source_artifact_path: str | None,
    internal_events_url: str,
    job_token: str,
    resource_profile: ResourceProfile = ResourceProfile.STANDARD,
    gpu_strategy: str = "ddp",
) -> dict[str, Any]:
    name = job_name(job_type, job_id)
    pod_labels = {
        "app.kubernetes.io/name": POD_LABEL_NAME,
        "lolday.job-id": str(job_id),
        "lolday.job-type": job_type.value,
    }

    init_containers = [_config_writer_init(job_id)]
    needs_source_model = job_type in (JobType.EVALUATE, JobType.PREDICT)
    if needs_source_model:
        if not source_run_id:
            raise ValueError("source_run_id required for evaluate/predict jobs")
        init_containers.append(
            _model_fetcher_init(
                mlflow_tracking_uri=mlflow_tracking_uri,
                source_run_id=source_run_id,
                source_artifact_path=source_artifact_path or "model",
            )
        )

    volumes = [
        {"name": "samples", "persistentVolumeClaim": {"claimName": "samples", "readOnly": True}},
        {"name": "config", "emptyDir": {"sizeLimit": "32Mi"}},
        {"name": "output", "emptyDir": {"sizeLimit": "10Gi"}},
        {"name": "source-model", "emptyDir": {"sizeLimit": "2Gi"}},
        {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi", "medium": "Memory"}},
    ]

    gpu_count = RESOURCE_PROFILE_GPU_COUNT[resource_profile]

    pod_spec = {
        "activeDeadlineSeconds": _active_deadline(job_type),
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "nodeSelector": {"kubernetes.io/hostname": settings.JOB_NODE_SELECTOR_HOSTNAME},
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "fsGroup": 1000,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumes": volumes,
        "initContainers": init_containers,
        "containers": [
            _detector_container(
                detector_image=detector_image,
                action=job_type.value,
                mlflow_tracking_uri=mlflow_tracking_uri,
                mlflow_run_id=mlflow_run_id,
                mlflow_experiment_id=mlflow_experiment_id,
                gpu_count=gpu_count,
                gpu_strategy=gpu_strategy,
            ),
            _event_tailer_sidecar(job_id, internal_events_url),
        ],
    }

    return {
        "apiVersion": "batch.volcano.sh/v1alpha1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": settings.JOB_NAMESPACE, "labels": pod_labels},
        "spec": {
            "schedulerName": "volcano",
            "minAvailable": 1,
            "queue": "lolday-training",
            "ttlSecondsAfterFinished": settings.JOB_TTL_SECONDS_AFTER_FINISHED,
            "tasks": [
                {
                    "name": "main",
                    "replicas": 1,
                    "policies": [
                        {"event": "TaskCompleted", "action": "CompleteJob"},
                        {"event": "PodFailed", "action": "AbortJob"},
                    ],
                    "template": {"metadata": {"labels": pod_labels}, "spec": pod_spec},
                }
            ],
        },
    }
```

Note the parameter change: `detector_cli_command` is GONE (every detector's entry point is now `maldet`). `model_name` is also gone (MLflow model registry is platform-owned; detector doesn't need to know the name). The call-site in `routers/jobs.py` must be updated to drop those two args.

- [ ] **Step 2b: Update call-site in `routers/jobs.py`**

Find where `build_volcano_job_manifest` is called. Update the kwargs:
- Remove `detector_cli_command=...`
- Remove `model_name=...`
- Add `internal_events_url=<constructed from settings.INTERNAL_EVENTS_BASE_URL + f"/internal/jobs/{job_id}/events">`
- Add `job_token=<the raw token from job_tokens.issue_token>`
- Add `gpu_strategy=<from manifest.lifecycle.supports_distributed or "ddp">`

The last one maps manifest's `supports_distributed` field to the Lightning strategy env. If `supports_distributed` is `False`, pass `"ddp"` (ignored when gpu_count=0 or 1). If it's `"ddp"`/`"fsdp"`/`"deepspeed"`, pass it through.

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/test_services_job_spec_phase11b.py -v
uv run pytest tests/test_jobs.py -v  # regression
git add backend/app/services/job_spec.py backend/app/routers/jobs.py backend/tests/test_services_job_spec_phase11b.py
git rm backend/tests/test_services_job_spec.py 2>/dev/null || true
git commit -m "feat(job_spec): phase 11b maldet-run + event-tailer sidecar"
```

---

## Task 9: Sidecar — `tail_events.py` in `job-helper`

**Files:**
- Create: `charts/lolday/helpers/job-helper/job_helper/tail_events.py`
- Modify: `charts/lolday/helpers/job-helper/pyproject.toml` (add httpx)
- Test: `charts/lolday/helpers/job-helper/tests/test_tail_events.py` (create tests/ dir if missing)

- [ ] **Step 1: Write the failing test**

```python
"""tail_events: tails an NDJSON file and POSTs each event to the backend."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from job_helper.tail_events import tail_and_post


@respx.mock
def test_tail_existing_events(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    # Write two events before tail starts (simulates detector already producing)
    out.write_text(
        json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}) + "\n"
        + json.dumps({"ts": "2026-04-24T00:01:00Z", "kind": "stage_end", "stage": "train", "status": "success"}) + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(return_value=Response(202))

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,  # test-only: returns when file is done
    )

    assert route.call_count == 2
    bodies = [json.loads(req.content) for req in route.calls]
    assert bodies[0]["kind"] == "stage_begin"
    assert bodies[1]["kind"] == "stage_end"


@respx.mock
def test_retry_on_transient_failure(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "metric", "name": "loss", "value": 0.1}) + "\n")

    # First 2 calls 503, third succeeds
    respx.post("http://backend/internal/jobs/x/events").mock(
        side_effect=[Response(503), Response(503), Response(202)]
    )

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,
    )
    # At least 3 attempts made
    assert len(respx.calls) >= 3
```

- [ ] **Step 2: Implement `job_helper/tail_events.py`**

```python
"""Tail an NDJSON event file and POST each line to the backend's internal events endpoint.

Designed as a Volcano Job sidecar container. When the detector's `events.jsonl` file is
written line-by-line (fsync per line), this tailer reads each appended line and forwards
it to the backend for persistence + WebSocket broadcast.

When the detector exits, the sidecar sees EOF. It continues to read for a grace period to
drain trailing events, then exits.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

GRACE_SECONDS = 30


def tail_and_post(
    *,
    events_path: Path,
    endpoint_url: str,
    job_token: str,
    stop_on_eof: bool = False,
    grace_seconds: int = GRACE_SECONDS,
    poll_interval_s: float = 0.5,
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    # Create if missing so .open works.
    events_path.touch(exist_ok=True)

    last_activity = time.monotonic()
    with httpx.Client(timeout=10.0) as client:
        with events_path.open("r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    last_activity = time.monotonic()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # skip malformed
                    _post_with_retry(client, endpoint_url, job_token, event)
                    continue

                if stop_on_eof:
                    return

                # EOF: wait, decide if we should exit
                if time.monotonic() - last_activity > grace_seconds:
                    # detector is done writing; drain complete
                    return
                time.sleep(poll_interval_s)


def _post_with_retry(client: httpx.Client, url: str, token: str, event: dict[str, Any]) -> None:
    delay = 0.5
    for attempt in range(6):  # 6 attempts over ~30 s
        try:
            resp = client.post(
                url,
                json=event,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


def _main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: python -m job_helper.tail_events <path/to/events.jsonl>\n")
        sys.exit(2)
    events_path = Path(args[0])
    endpoint_url = os.environ["INTERNAL_EVENTS_URL"]
    job_token = os.environ["JOB_TOKEN"]
    tail_and_post(events_path=events_path, endpoint_url=endpoint_url, job_token=job_token)


if __name__ == "__main__":
    _main()
```

- [ ] **Step 3: Add httpx to `charts/lolday/helpers/job-helper/pyproject.toml`**

Check the file; add `"httpx>=0.28.0"` to `dependencies` if not already present (write_config / fetch_model may have needed it).

- [ ] **Step 4: Run tests + commit**

```bash
cd charts/lolday/helpers/job-helper
uv run pytest tests/test_tail_events.py -v
cd /path/to/lolday-phase11b
git add charts/lolday/helpers/job-helper/
git commit -m "feat(job-helper): tail_events sidecar (phase 11b event stream)"
```

- [ ] **Step 5: Build + push `job-helper:v3` image**

```bash
cd charts/lolday/helpers/job-helper
docker build -t harbor.lolday.svc.cluster.local:80/lolday/job-helper:v3 .
# Get Harbor push creds (same as Phase 9.3 flow — see deploy.sh)
# docker login ... && docker push ...
```

Actually — the lolday build pipeline was switched to BuildKit in Phase 9.3. The `job-helper` image is a helper, not a detector, so it's OK to build locally with `docker build` as a one-shot (Phase 8 did this too, see the deploy.sh steps). Instructions for Harbor push:

```bash
# Login with robot credentials
HARBOR_ROBOT_TOKEN=$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq -r '.auths[].auth' | base64 -d | cut -d: -f2)
docker login harbor.lolday.svc.cluster.local:80 -u 'robot$build-pusher' -p "$HARBOR_ROBOT_TOKEN"
docker push harbor.lolday.svc.cluster.local:80/lolday/job-helper:v3
docker logout harbor.lolday.svc.cluster.local:80
```

The user may need `sudo` for `docker` if their user isn't in the `docker` group. Ask the user to run the commands if so.

---

## Task 10: `services/events_tail.py` — persistence + in-process broadcast

**Files:**
- Create: `backend/app/services/events_tail.py`
- Test: `backend/tests/test_services_events_tail.py`

- [ ] **Step 1: Write the failing test**

```python
"""events_tail: persist an event into job_events + broadcast to subscribers."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobEvent
from app.services.events_tail import EventBroker, persist_event


@pytest.mark.asyncio
async def test_persist_event_inserts_row(async_session: AsyncSession, seed_job) -> None:
    job = await seed_job()
    event = {
        "ts": "2026-04-24T00:00:00Z",
        "kind": "metric",
        "name": "train_loss",
        "value": 0.34,
        "step": 1,
    }
    await persist_event(async_session, job_id=job.id, event=event)
    rows = (await async_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))).all()
    assert len(rows) == 1
    assert rows[0].kind == "metric"
    assert rows[0].payload["name"] == "train_loss"


@pytest.mark.asyncio
async def test_broadcast_delivers_to_subscriber(seed_job) -> None:
    job = await seed_job()
    broker = EventBroker()
    queue: asyncio.Queue = broker.subscribe(job.id)
    event = {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}
    await broker.publish(job.id, event)
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received == event


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue() -> None:
    broker = EventBroker()
    jid = uuid.uuid4()
    q = broker.subscribe(jid)
    broker.unsubscribe(jid, q)
    assert jid not in broker._subscribers  # internal, acceptable for test
```

`seed_job` fixture should already exist in `conftest.py`. If not, add one that creates a minimal Job + parent rows.

- [ ] **Step 2: Implement `app/services/events_tail.py`**

```python
"""Phase 11b event stream: persistence + in-process WebSocket broadcast."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobEvent


async def persist_event(
    session: AsyncSession, *, job_id: uuid.UUID, event: dict[str, Any]
) -> JobEvent:
    ts = _parse_ts(event.get("ts")) or datetime.utcnow()
    kind = event.get("kind") or "unknown"
    payload = {k: v for k, v in event.items() if k not in ("ts", "kind")}
    row = JobEvent(job_id=job_id, ts=ts, kind=kind, payload=payload)
    session.add(row)
    await session.commit()
    return row


def _parse_ts(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class EventBroker:
    """In-process fan-out of events to WebSocket subscribers.

    One :class:`asyncio.Queue` per (job_id, subscriber). The POST handler calls
    :meth:`publish`; WebSocket handlers call :meth:`subscribe` to get their
    queue, then drain it forever until the client disconnects.
    """

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, job_id: uuid.UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[job_id].append(q)
        return q

    def unsubscribe(self, job_id: uuid.UUID, q: asyncio.Queue) -> None:
        queues = self._subscribers.get(job_id)
        if queues and q in queues:
            queues.remove(q)
        if queues is not None and not queues:
            self._subscribers.pop(job_id, None)

    async def publish(self, job_id: uuid.UUID, event: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(job_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the slowest subscriber's oldest event to prevent unbounded memory.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(event)


# Module-level singleton shared across requests in the backend process.
event_broker = EventBroker()
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/test_services_events_tail.py -v
git add backend/app/services/events_tail.py backend/tests/test_services_events_tail.py
git commit -m "feat(services): events_tail — persist + in-process broadcast"
```

---

## Task 11: Internal events POST endpoint

**Files:**
- Modify: `backend/app/routers/internal.py`
- Test: `backend/tests/test_internal_events.py`

- [ ] **Step 1: Write the failing test**

```python
"""POST /internal/jobs/{id}/events — sidecar authenticated via job token."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_event_persists_and_broadcasts(client: AsyncClient, seed_job_with_token) -> None:
    job, raw_token = await seed_job_with_token()
    event = {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}
    resp = await client.post(
        f"/internal/jobs/{job.id}/events",
        json=event,
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_post_event_rejects_invalid_token(client: AsyncClient, seed_job_with_token) -> None:
    job, _ = await seed_job_with_token()
    resp = await client.post(
        f"/internal/jobs/{job.id}/events",
        json={"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_event_rejects_wrong_job_id(client: AsyncClient, seed_job_with_token) -> None:
    import uuid
    job, token = await seed_job_with_token()
    other_id = uuid.uuid4()
    resp = await client.post(
        f"/internal/jobs/{other_id}/events",
        json={"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (401, 404)
```

`seed_job_with_token` may need to be added to conftest; it should create a Job + store a hashed token via `app.services.job_tokens.issue_token`.

- [ ] **Step 2: Add endpoint to `app/routers/internal.py`**

```python
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_job_token
from app.models import Job
from app.services.events_tail import event_broker, persist_event


@router.post("/jobs/{job_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    job_id: uuid.UUID,
    event: dict[str, Any],
    job: Job = Depends(require_job_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    if job.id != job_id:
        raise HTTPException(status_code=404, detail="job_id mismatch")
    await persist_event(session, job_id=job.id, event=event)
    await event_broker.publish(job.id, event)
    return {"accepted": True}
```

Verify `require_job_token` from `app.deps` returns the Job object keyed by the Bearer token (Phase 4 already wired this).

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/test_internal_events.py -v
git add backend/app/routers/internal.py backend/tests/test_internal_events.py
git commit -m "feat(internal): POST /internal/jobs/{id}/events (sidecar ingress)"
```

---

## Task 12: GET /jobs/{id}/events — paged retrieval

**Files:**
- Modify: `backend/app/routers/jobs.py`
- Create: `backend/app/schemas/job_event.py`
- Test: `backend/tests/test_jobs_events_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
"""GET /jobs/{id}/events — paged historical retrieval."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_events_empty(client: AsyncClient, seed_job_for_user) -> None:
    job, auth_headers = await seed_job_for_user()
    resp = await client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"events": [], "next_since": None}


@pytest.mark.asyncio
async def test_get_events_ordered_by_ts(client: AsyncClient, seed_job_for_user, seed_events) -> None:
    job, auth_headers = await seed_job_for_user()
    await seed_events(job.id, [
        {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        {"ts": "2026-04-24T00:00:05Z", "kind": "metric", "name": "loss", "value": 0.3},
        {"ts": "2026-04-24T00:00:10Z", "kind": "stage_end", "stage": "train", "status": "success"},
    ])
    resp = await client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert [e["kind"] for e in data["events"]] == ["stage_begin", "metric", "stage_end"]


@pytest.mark.asyncio
async def test_get_events_since_cursor(client: AsyncClient, seed_job_for_user, seed_events) -> None:
    job, auth_headers = await seed_job_for_user()
    await seed_events(job.id, [
        {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        {"ts": "2026-04-24T00:00:10Z", "kind": "stage_end", "stage": "train", "status": "success"},
    ])
    resp = await client.get(
        f"/api/v1/jobs/{job.id}/events",
        params={"since": "2026-04-24T00:00:05Z"},
        headers=auth_headers,
    )
    data = resp.json()
    assert len(data["events"]) == 1
    assert data["events"][0]["kind"] == "stage_end"


@pytest.mark.asyncio
async def test_get_events_rejects_non_owner(client: AsyncClient, seed_job_for_user, foreign_user_headers) -> None:
    job, _ = await seed_job_for_user()
    resp = await client.get(f"/api/v1/jobs/{job.id}/events", headers=foreign_user_headers)
    assert resp.status_code in (403, 404)
```

- [ ] **Step 2: Write `app/schemas/job_event.py`**

```python
"""Pydantic response schemas for job_events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    kind: str
    payload: dict[str, Any]


class JobEventsPage(BaseModel):
    events: list[JobEventOut]
    next_since: datetime | None
```

- [ ] **Step 3: Implement endpoint in `app/routers/jobs.py`**

Find the existing `/api/v1/jobs/{job_id}` router. Add:

```python
from datetime import datetime
from sqlalchemy import select
from app.models import JobEvent
from app.schemas.job_event import JobEventOut, JobEventsPage


@router.get("/{job_id}/events", response_model=JobEventsPage)
async def list_job_events(
    job_id: uuid.UUID,
    since: datetime | None = None,
    limit: int = 500,
    session: AsyncSession = Depends(get_async_session),
    job: Job = Depends(require_job_access),  # existing owner-only dep from Phase 4
) -> JobEventsPage:
    stmt = select(JobEvent).where(JobEvent.job_id == job.id)
    if since is not None:
        stmt = stmt.where(JobEvent.ts > since)
    stmt = stmt.order_by(JobEvent.ts.asc()).limit(limit)
    rows = list(await session.scalars(stmt))
    next_since = rows[-1].ts if rows and len(rows) == limit else None
    return JobEventsPage(
        events=[JobEventOut.model_validate(r) for r in rows],
        next_since=next_since,
    )
```

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/test_jobs_events_endpoint.py -v
git add backend/app/routers/jobs.py backend/app/schemas/job_event.py backend/tests/test_jobs_events_endpoint.py
git commit -m "feat(jobs): GET /jobs/{id}/events (paged historical retrieval)"
```

---

## Task 13: WebSocket /jobs/{id}/events — live stream

**Files:**
- Modify: `backend/app/routers/jobs.py` (add WS endpoint)
- Test: `backend/tests/test_jobs_events_websocket.py`

- [ ] **Step 1: Write the failing test**

```python
"""WS /jobs/{id}/events — live event stream."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient


def test_ws_receives_published_event(client_sync: TestClient, seed_job_for_user_sync) -> None:
    job, cookie = seed_job_for_user_sync()
    with client_sync.websocket_connect(
        f"/api/v1/jobs/{job.id}/events",
        cookies={"lolday_session": cookie},
    ) as ws:
        # Publish an event via the broker
        from app.services.events_tail import event_broker

        asyncio.run(event_broker.publish(job.id, {"ts": "2026-04-24T00:00:00Z", "kind": "metric", "name": "loss", "value": 0.5}))

        msg = ws.receive_json()
        assert msg["kind"] == "metric"
        assert msg["name"] == "loss"


def test_ws_rejects_non_owner(client_sync: TestClient, seed_job_for_user_sync, foreign_cookie) -> None:
    job, _ = seed_job_for_user_sync()
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client_sync.websocket_connect(
            f"/api/v1/jobs/{job.id}/events",
            cookies={"lolday_session": foreign_cookie},
        ) as ws:
            ws.receive_json()
```

(The sync client fixtures and cookie-based auth helpers will need conftest additions; reuse Phase 10 patterns where cookie-based auth was wired in.)

- [ ] **Step 2: Implement WebSocket endpoint in `app/routers/jobs.py`**

```python
from fastapi import WebSocket, WebSocketDisconnect
from app.services.events_tail import event_broker


@router.websocket("/{job_id}/events")
async def websocket_job_events(
    websocket: WebSocket,
    job_id: uuid.UUID,
) -> None:
    # Phase 10 auth is SSO-via-cookie; enforcing it at WS level needs resolving the
    # ``Cf-Access-Jwt-Assertion`` header from the cloudflared proxy. Since this is
    # single-node behind Cloudflare Access, we reuse the existing `current_active_user`
    # machinery manually.
    from app.users import cf_access_user
    from app.db import async_session_maker
    from app.models import Job

    # Extract headers like the HTTP dep; raise 401 via close().
    try:
        # Minimal auth — verify the JWT the Phase 10 way.
        from app.auth_cf import verify_jwt  # introduce small extraction if not present
        claims = await verify_jwt(websocket.headers)
    except Exception:
        await websocket.close(code=4401)
        return

    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            await websocket.close(code=4404)
            return
        # Verify ownership (reuse logic from require_job_access)
        from app.deps import _user_owns_job
        if not await _user_owns_job(session, job, claims["email"]):
            await websocket.close(code=4403)
            return

    await websocket.accept()
    queue = event_broker.subscribe(job_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        event_broker.unsubscribe(job_id, queue)
```

Notes on WS auth: FastAPI's WS auth via `Depends` has historically been finicky with cookie sessions. The manual extraction is fine for v1 — extract a reusable `verify_jwt_from_ws(websocket)` helper into `app/auth_cf.py` if Phase 10 has a similar helper. If extracting is too much, the simplest path for v1 is: issue a **short-lived job-scoped JWT** for the WS connection via a prior HTTP endpoint (`POST /jobs/{id}/events/token`), and have the frontend pass it as a query param `?token=...`. This avoids WS-cookie-auth complexity. Decide at implementation time based on what Phase 10 already provides.

**Recommended v1 path:** reuse Phase 10's `Cf-Access-Jwt-Assertion` header — it flows through cloudflared to the WS upgrade request. Verify the JWT in the WS handler the same way `cf_access_user` dep does for HTTP.

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/test_jobs_events_websocket.py -v
git add backend/app/routers/jobs.py backend/tests/test_jobs_events_websocket.py
git commit -m "feat(jobs): WS /jobs/{id}/events (phase 11b live stream)"
```

---

## Task 14: Reconciler — status determination from stage_end events

**Files:**
- Modify: `backend/app/reconciler.py`
- Test: existing `backend/tests/test_reconciler_jobs.py` — add new cases

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reconciler_jobs.py`:

```python
async def test_status_from_stage_end_success(async_session, seed_job_running) -> None:
    """When a job has a `stage_end` event with status=success, mark the job SUCCEEDED
    even if Volcano phase is still `Running` (race between pod exit and Volcano poll)."""
    from datetime import datetime, timezone

    from app.models import JobEvent, JobStatus
    from app.reconciler import reconcile_job

    job = await seed_job_running()
    async_session.add(JobEvent(
        job_id=job.id,
        ts=datetime.now(timezone.utc),
        kind="stage_end",
        payload={"stage": "train", "status": "success"},
    ))
    await async_session.commit()

    # Stub Volcano to still say Running; reconciler must trust the event.
    with _patched_volcano_phase("Running"):
        await reconcile_job(async_session, job)

    await async_session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED
```

(Assumes `_patched_volcano_phase` helper exists in the test module from Phase 7.3 / Phase 7.5; adapt to match.)

- [ ] **Step 2: Add event-based terminal detection to `reconcile_job`**

Before the existing Volcano-phase dispatch, check for a recent `stage_end` event:

```python
async def _check_event_terminal(session: AsyncSession, job_id: uuid.UUID) -> str | None:
    """Return 'success' / 'failure' / None based on the most recent stage_end event."""
    from sqlalchemy import select
    from app.models import JobEvent

    stmt = (
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.kind == "stage_end")
        .order_by(JobEvent.ts.desc())
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    if row is None:
        return None
    status = (row.payload or {}).get("status")
    return status if status in ("success", "failure") else None
```

Integrate into `reconcile_job`:

```python
    event_status = await _check_event_terminal(session, j.id)
    if event_status == "success":
        await _handle_job_succeeded(session, j)
        return
    if event_status == "failure":
        await _handle_job_failed(session, j)
        return

    # ... existing Volcano phase check as fallback ...
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/test_reconciler_jobs.py -v
git add backend/app/reconciler.py backend/tests/test_reconciler_jobs.py
git commit -m "feat(reconciler): trust stage_end event before Volcano phase"
```

---

## Task 15: Frontend — live metric chart + event log

**Files:**
- Create: `frontend/src/hooks/useJobEvents.ts`
- Create: `frontend/src/components/JobMetricChart.tsx`
- Modify: `frontend/src/pages/JobDetail.tsx`
- Test: `frontend/src/pages/JobDetail.test.tsx` (Playwright spec if consistent with Phase 5)

Given the existing frontend test strategy is Playwright E2E (Phase 5), the unit tests here are lighter. Follow the codebase convention.

- [ ] **Step 1: Write `hooks/useJobEvents.ts`**

```typescript
import { useEffect, useState } from "react";

export type MaldetEvent = {
  ts: string;
  kind: string;
  [k: string]: unknown;
};

export function useJobEvents(jobId: string | null): MaldetEvent[] {
  const [events, setEvents] = useState<MaldetEvent[]>([]);

  useEffect(() => {
    if (!jobId) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${window.location.host}/api/v1/jobs/${jobId}/events`);

    ws.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data) as MaldetEvent;
        setEvents((prev) => [...prev, event]);
      } catch {
        // ignore malformed events
      }
    };

    ws.onerror = () => {
      /* logged by browser */
    };

    return () => {
      ws.close();
    };
  }, [jobId]);

  return events;
}
```

- [ ] **Step 2: Write `components/JobMetricChart.tsx`**

```tsx
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import type { MaldetEvent } from "@/hooks/useJobEvents";

type Point = { step: number; [metric: string]: number };

function metricsToSeries(events: MaldetEvent[]): Point[] {
  const byStep = new Map<number, Point>();
  for (const e of events) {
    if (e.kind !== "metric") continue;
    const step = typeof e.step === "number" ? e.step : 0;
    const name = String(e.name ?? "value");
    const value = typeof e.value === "number" ? e.value : Number.NaN;
    if (Number.isNaN(value)) continue;
    const row = byStep.get(step) ?? { step };
    row[name] = value;
    byStep.set(step, row);
  }
  return [...byStep.values()].sort((a, b) => a.step - b.step);
}

export function JobMetricChart({ events }: { events: MaldetEvent[] }) {
  const data = metricsToSeries(events);
  const metrics = new Set<string>();
  for (const d of data) for (const k of Object.keys(d)) if (k !== "step") metrics.add(k);
  if (data.length === 0) {
    return <p className="text-sm text-muted-foreground">No metrics yet.</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="step" />
        <YAxis />
        <Tooltip />
        <Legend />
        {[...metrics].map((m, i) => (
          <Line key={m} type="monotone" dataKey={m} stroke={`hsl(${(i * 70) % 360}, 70%, 45%)`} dot={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 3: Wire into `JobDetail.tsx`**

Find the existing JobDetail page. Inside the render tree, for jobs in status `running`, `pending`, or `preparing`, add:

```tsx
import { useJobEvents } from "@/hooks/useJobEvents";
import { JobMetricChart } from "@/components/JobMetricChart";
// ...
const events = useJobEvents(job.status === "succeeded" || job.status === "failed" ? null : jobId);
// ...
{events.length > 0 && (
  <section>
    <h2>Live metrics</h2>
    <JobMetricChart events={events} />
  </section>
)}
```

- [ ] **Step 4: Install recharts if not present**

```bash
cd frontend
npm install recharts
```

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): live metric chart + event stream (phase 11b)"
```

---

## Task 16: Chart + values.yaml bumps

**Files:**
- Modify: `charts/lolday/Chart.yaml` (appVersion + version bump)
- Modify: `charts/lolday/values.yaml` (jobHelper v2 → v3, + new env for backend `INTERNAL_EVENTS_BASE_URL`)
- Modify: `charts/lolday/templates/backend.yaml` (add `INTERNAL_EVENTS_BASE_URL` env)
- Modify: `scripts/deploy.sh` (backend image → phase11b, jobHelper reference → v3)

- [ ] **Step 1: Chart.yaml**

```yaml
version: 0.13.0
appVersion: "phase11b"
```

- [ ] **Step 2: values.yaml**

Find the `jobHelperImage: harbor.lolday.svc:80/lolday/job-helper:v2` line; change to `:v3`.

Add (near backend env block):

```yaml
backend:
  env:
    INTERNAL_EVENTS_BASE_URL: "http://backend:8000"
```

- [ ] **Step 3: backend.yaml template**

Add to the env list:

```yaml
        - name: INTERNAL_EVENTS_BASE_URL
          value: {{ .Values.backend.env.INTERNAL_EVENTS_BASE_URL | quote }}
```

- [ ] **Step 4: deploy.sh**

Bump `BACKEND_IMAGE` default to `phase11b`. Verify `jobHelperImage` helm --set overrides if used.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/ scripts/deploy.sh
git commit -m "chore(chart): bump to 0.13.0, job-helper v3, backend phase11b, +INTERNAL_EVENTS_BASE_URL"
```

---

## Task 17: Deploy + local E2E checklist

**Files:**
- Create: `docs/phase11b-e2e-checklist.md`

- [ ] **Step 1: Build + push backend image phase11b**

```bash
cd /path/to/lolday-phase11b
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11b backend/
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11b
```

Ask the user to run if `docker` requires sudo.

- [ ] **Step 2: Deploy chart**

```bash
source ~/.lolday-secrets.env
bash scripts/deploy.sh
```

Alembic hook runs and applies migrations. Verify `job_events` table exists and `detector_version.manifest` column exists:

```bash
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c '\d job_events'
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c '\d detector_version' | grep manifest
```

- [ ] **Step 3: Scaffold a smoke detector**

```bash
cd /tmp
maldet scaffold --template rf --name smoketest --out ./smoketest
cd smoketest
# Replace features with trivial implementation as in Phase 11a integration tests
# Edit src/smoketest/features.py to return a constant ndarray
pip install -e .
maldet check
maldet describe
```

- [ ] **Step 4: Build + push smoketest detector to Harbor**

```bash
# Manual BuildKit build with labels (or use lolday's build pipeline — if available)
MALDET_MANIFEST_B64=$(maldet describe --format json | base64 -w0)
docker build \
  --build-arg MALDET_NAME=smoketest \
  --build-arg MALDET_VERSION=0.1.0 \
  --build-arg MALDET_FRAMEWORK=sklearn \
  --build-arg MALDET_MANIFEST_B64="$MALDET_MANIFEST_B64" \
  --build-arg GIT_COMMIT=$(git rev-parse HEAD) \
  -t harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0 .
docker push harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0
```

- [ ] **Step 5: Register detector in lolday + trigger build**

Register via the frontend or curl; submit a training job; verify:

1. `detector_version` row has `manifest` populated
2. `job_events` rows appear as the detector runs
3. WS `/api/v1/jobs/{id}/events` streams events to a browser-connected client (open job page, watch the live chart)
4. Reconciler marks the job succeeded based on `stage_end` event
5. MLflow run + model registry populated as in Phase 4

- [ ] **Step 6: Write the E2E checklist**

Document the above steps in `docs/phase11b-e2e-checklist.md` for future verification.

- [ ] **Step 7: Commit**

```bash
git add docs/phase11b-e2e-checklist.md
git commit -m "docs: phase 11b E2E checklist"
```

---

## Task 18: PR + merge

- [ ] **Step 1: Run full test suite**

```bash
cd backend
uv run pytest
```

All tests green. Coverage should not regress.

- [ ] **Step 2: Push branch + open PR**

```bash
git push -u origin phase-11b-impl
gh pr create --base main --head phase-11b-impl \
  --title "feat: phase 11b — lolday backend detector contract rewrite" \
  --body "$(cat <<'EOF'
## Summary

Rewrite the lolday backend's detector-platform contract to consume `maldet` v1:

- `pyproject.toml`: pin `maldet ~= 1.0`
- Alembic: new `job_events` table + `detector_version.manifest` JSONB column
- `services/harbor.py`: `get_image_labels()` reads `io.maldet.manifest` OCI label
- `services/manifest_store.py`: decode base64 → `DetectorManifest` (imported from `maldet`)
- `services/validator.py`: job pre-flight (resource_profile / dataset_contract / stage)
- `services/job_config.py`: renders Hydra YAML (replaces Phase 4 JSON renderer)
- `services/job_spec.py`: `maldet run <stage>` command + `event-tailer` sidecar
- `services/events_tail.py`: persist to `job_events` + in-process broadcast
- `routers/internal.py`: `POST /internal/jobs/{id}/events` (job-token auth)
- `routers/jobs.py`: `GET /jobs/{id}/events` + `WS /jobs/{id}/events`
- `reconciler.py`: status determination from `stage_end` event
- `charts/lolday/helpers/job-helper`: `tail_events.py` sidecar command (image v3)
- `frontend/src/pages/JobDetail.tsx`: live Recharts metric chart + event log
- Chart 0.12.1 → 0.13.0, backend image phase10 → phase11b, job-helper v2 → v3

## Test plan

- [ ] `pytest` green (unit + regression)
- [ ] `alembic upgrade head` applies on dev DB without error
- [ ] `docs/phase11b-e2e-checklist.md` manual run on server30 passes
- [ ] Live metric chart renders from a real training job's events.jsonl
- [ ] Reconciler trusts `stage_end.status` over Volcano phase
- [ ] v0 detectors (manifest-less) are rejected at build time with `manifest_label_missing`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Review + merge**

Use `pr-review-toolkit:review-pr` on the PR to run the five-agent review pattern (code / tests / errors / types / comments). Address findings in follow-up commits on the same branch. Then squash-merge to `main`.

Remove the `phase-11b-impl` branch + worktree after merge:

```bash
git worktree remove ../lolday-phase11b
git branch -D phase-11b-impl  # local
git push origin --delete phase-11b-impl  # remote (optional; GitHub UI can also delete on merge)
```

---

## Self-Review Checklist

**Spec coverage (§3F lolday Backend Changes):**

| Spec item | Task |
|---|---|
| `services/job_spec.py` — `maldet run` + sidecar | Task 8 |
| `services/job_config.py` — Hydra YAML + overrides | Task 7 |
| `services/harbor.py` — read Labels field | Task 4 |
| `services/validator.py` — manifest + resource + dataset_contract pre-flight | Task 6 |
| `services/events_tail.py` — sidecar HTTP receive + persist | Task 10 |
| `models/job_event.py` — ORM | Task 3 |
| Alembic migration | Task 2 |
| `routers/internal.py` — POST /internal/jobs/{id}/events | Task 11 |
| `routers/jobs.py` — GET paged + WS stream | Tasks 12 + 13 |
| Reconciler — status from `stage_end.status` | Task 14 |
| `frontend/src/pages/JobDetail.tsx` — live metric chart | Task 15 |
| Chart / deploy / values | Task 16 |
| E2E checklist | Task 17 |

All mapped.

**Placeholder scan:** None.

**Type consistency:** `DetectorManifest` is imported from `maldet.manifest` in all backend code (Tasks 5, 6, 5b). `event_broker` is the shared module-level instance used by both `routers/internal.py` (publish) and `routers/jobs.py` (subscribe). `JobEvent` ORM fields match across Task 3 (creation) and Tasks 10/11/12 (read/write).

**Scope check:** All tasks target the lolday backend / chart / frontend for a single coherent feature (the detector contract v1). No sub-scoping needed.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-24-phase11b-lolday-backend-contract.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, spec + quality reviews between tasks, fast iteration. Phase 11a established the pattern.

**2. Inline Execution** — batch execute in this session via superpowers:executing-plans with checkpoints.

**Which approach?**
