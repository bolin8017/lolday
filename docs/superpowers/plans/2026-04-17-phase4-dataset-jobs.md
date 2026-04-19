# Phase 4: Dataset & Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver end-to-end train/evaluate/predict jobs with dataset management, MLflow tracking, and Model Registry. Lab users submit a detector+dataset+params, get reproducible artifacts back.

**Architecture:** FastAPI backend creates K8s Jobs per submitted job; asyncio reconciler (extends Phase 3) polls K8s Job + MLflow state; detector images run their own `maldet`-provided CLI which (after this phase's maldet PR) auto-logs to MLflow when `MLFLOW_TRACKING_URI` env is set. Sample files are mounted via hostPath PV (ReadOnly). Job pods are egress-restricted to DNS + MLflow + backend only.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Kubernetes Python client, httpx, jsonschema, MLflow 2.20, PostgreSQL 16, local-path PV.

**Spec:** `docs/superpowers/specs/2026-04-17-phase4-dataset-jobs-design.md`

**Server:** server30 (Ubuntu 24.04, K3s v1.34.6+k3s1, 2× RTX 2080 Ti)

**Constraints:**
- `bolin8017` has no persistent sudo; give sudo commands to user to run
- CLI tools in `~/.local/bin/`; do NOT system-install anything without explicit approval
- SSH (port 9453) must never be disrupted; K3s must remain running after every step
- No Cilium / no CNI changes (Phase 3 Amendment A1 still applies)
- Detector image changes require Phase 3 rebuild flow (we do not bypass)

---

## File Structure

Backend additions:

```
backend/
├── pyproject.toml                        # + jsonschema, + mlflow-client (small)
├── alembic/versions/
│   └── xxx_add_phase4_tables.py          # NEW
├── app/
│   ├── main.py                           # MODIFY (new routers + env)
│   ├── config.py                         # MODIFY (MLflow URL, limits, paths)
│   ├── deps.py                           # MODIFY (+ require_job_access, require_job_token)
│   ├── reconciler.py                     # MODIFY (add job + model-sync passes)
│   │
│   ├── models/
│   │   ├── __init__.py                   # MODIFY (export new models)
│   │   ├── dataset.py                    # NEW
│   │   ├── job.py                        # NEW
│   │   ├── model_registry.py             # NEW (ModelVersion, ModelTransitionLog)
│   │   └── detector.py                   # MODIFY (+ mlflow_experiment_id column)
│   │
│   ├── schemas/
│   │   ├── __init__.py                   # MODIFY
│   │   ├── dataset.py                    # NEW
│   │   ├── job.py                        # NEW
│   │   └── model_registry.py             # NEW
│   │
│   ├── routers/
│   │   ├── datasets.py                   # NEW
│   │   ├── jobs.py                       # NEW
│   │   ├── models_registry.py            # NEW
│   │   ├── experiments_proxy.py          # NEW (MLflow proxy)
│   │   └── internal.py                   # MODIFY (+ /internal/jobs/{id}/config)
│   │
│   └── services/
│       ├── mlflow_client.py              # NEW (thin httpx wrapper)
│       ├── dataset.py                    # NEW (CSV parse + checksum + integrity)
│       ├── job_config.py                 # NEW (render resolved_config)
│       ├── job_spec.py                   # NEW (K8s Job manifest generator)
│       ├── job_tokens.py                 # NEW (one-time tokens, hashed storage)
│       └── model_registry.py             # NEW (transitions + sync)
│
└── tests/
    ├── conftest.py                       # MODIFY (mock MLflow, expand K8s mock)
    ├── fixtures/
    │   ├── sample_dataset.csv            # NEW (small valid CSV)
    │   └── sample_mlflow_responses.py    # NEW
    ├── test_services_mlflow_client.py    # NEW
    ├── test_services_dataset.py          # NEW
    ├── test_services_job_config.py       # NEW
    ├── test_services_job_spec.py         # NEW
    ├── test_services_job_tokens.py       # NEW
    ├── test_services_model_registry.py   # NEW
    ├── test_reconciler_jobs.py           # NEW
    ├── test_datasets.py                  # NEW
    ├── test_jobs.py                      # NEW
    ├── test_models_registry.py           # NEW
    └── test_experiments_proxy.py         # NEW
```

Helm chart additions:

```
charts/lolday/
├── values.yaml                           # MODIFY (+ mlflow, samples, jobs)
├── templates/
│   ├── mlflow.yaml                        # NEW
│   ├── mlflow-db-init-job.yaml            # NEW
│   ├── mlflow-secret.yaml                 # NEW
│   ├── samples-pv.yaml                    # NEW (hostPath PVs, in chart-managed form)
│   ├── samples-pvc.yaml                   # NEW
│   ├── job-networkpolicy.yaml             # NEW
│   ├── backend.yaml                       # MODIFY (+ MLflow env)
│   └── backend-rbac.yaml                  # MODIFY (+ PV/PVC + jobs watch)
└── helpers/
    └── job-helper/                        # NEW
        ├── Dockerfile
        ├── pyproject.toml
        └── job_helper/
            ├── __init__.py
            ├── write_config.py            # NEW
            └── fetch_model.py             # NEW
```

Scripts:

```
scripts/
└── deploy.sh                              # MODIFY (+ samples dirs check, + mlflow wait)
```

External repos (separate PRs, tracked as Task 17 + Task 18):

```
islab-malware-detector/
├── pyproject.toml                        # MODIFY (+ mlflow optional extra)
├── src/maldet/cli.py                     # MODIFY (+ MLflow-gated wrapping)
└── tests/test_cli_mlflow.py              # NEW

upxelfdet/
└── pyproject.toml                        # MODIFY (bump maldet dep; add [mlflow] extra)
```

---

## Task 1: Backend Scaffolding — Dependencies, Models/Schemas Split

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/models/dataset.py` (placeholder)
- Create: `backend/app/models/job.py` (placeholder)
- Create: `backend/app/models/model_registry.py` (placeholder)
- Create: `backend/app/schemas/dataset.py` (placeholder)
- Create: `backend/app/schemas/job.py` (placeholder)
- Create: `backend/app/schemas/model_registry.py` (placeholder)
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/schemas/__init__.py`

- [ ] **Step 1: Add dependencies**

Edit `backend/pyproject.toml`, add to `dependencies`:

```toml
    "jsonschema>=4.23.0",
    "mlflow-skinny>=2.20.0",
```

`mlflow-skinny` is MLflow without server/UI — for the tracking client only. Prefer over full `mlflow` because we just need the REST API. `jsonschema` is for validating user-supplied `params` against `detector_version.config_schema`.

Run:

```bash
cd backend && uv sync
```

Expected: new packages installed without errors. `uv.lock` updated.

- [ ] **Step 2: Create empty model files (real content in Task 2)**

Create `backend/app/models/dataset.py`:

```python
"""Dataset config model. Content added in Task 2."""
```

Create `backend/app/models/job.py`:

```python
"""Job model. Content added in Task 2."""
```

Create `backend/app/models/model_registry.py`:

```python
"""Model Registry pointer tables. Content added in Task 2."""
```

- [ ] **Step 3: Create empty schema files**

Create `backend/app/schemas/dataset.py`:

```python
"""Dataset Pydantic schemas. Content added in Task 5."""
```

Create `backend/app/schemas/job.py`:

```python
"""Job Pydantic schemas. Content added in Task 9."""
```

Create `backend/app/schemas/model_registry.py`:

```python
"""Model Registry Pydantic schemas. Content added in Task 11."""
```

- [ ] **Step 4: Update models `__init__.py`**

Replace `backend/app/models/__init__.py` with:

```python
from app.models.credential import UserGitCredential
from app.models.dataset import DatasetConfig
from app.models.detector import Detector, DetectorBuild, DetectorVersion
from app.models.job import Job, JobStatus, JobType
from app.models.model_registry import (
    ModelTransitionLog,
    ModelVersion,
    ModelVersionStage,
)
from app.models.user import Base, Role, User

__all__ = [
    "Base",
    "Role",
    "User",
    "UserGitCredential",
    "Detector",
    "DetectorVersion",
    "DetectorBuild",
    "DatasetConfig",
    "Job",
    "JobStatus",
    "JobType",
    "ModelVersion",
    "ModelVersionStage",
    "ModelTransitionLog",
]
```

This will fail until Task 2 creates the classes — accept transient breakage during the task chain; commit at the END of Task 2.

- [ ] **Step 5: Update schemas `__init__.py`**

Replace `backend/app/schemas/__init__.py` (append the new exports at bottom; keep Phase 2/3 content intact):

```python
# ... existing imports ...
from app.schemas.dataset import (
    DatasetConfigCreate,
    DatasetConfigRead,
    DatasetConfigUpdate,
)
from app.schemas.job import JobCreate, JobRead, JobSummary
from app.schemas.model_registry import (
    ModelTransitionRequest,
    ModelVersionRead,
)
```

Again these will fail until Tasks 5/9/11 define the classes — it's fine; we commit at end of those tasks.

- [ ] **Step 6: Run existing tests to sanity-check**

```bash
cd backend && uv run pytest tests/test_auth.py tests/test_admin.py tests/test_credentials.py tests/test_detectors.py -v 2>&1 | tail -30
```

Expected: existing tests still pass (imports from `app.models`/`app.schemas` that touch the new placeholders won't be triggered because the `__init__.py` imports will fail — **expected** at this stage).

**If tests fail because of the `__init__.py` imports:** temporarily comment out the new lines in `__init__.py`; uncomment at the END of Task 2. The task boundary is "Task 1 adds deps + creates placeholder files; Task 2 makes everything work".

Alternative, safer approach: keep the new imports commented out in `__init__.py` in Step 4-5; uncomment them in Task 2 Step 9.

Let's prefer the safer approach — use commented-out lines now, uncomment in Task 2.

Revised Step 4 (comment them out):

```python
from app.models.credential import UserGitCredential
# from app.models.dataset import DatasetConfig            # Task 2
from app.models.detector import Detector, DetectorBuild, DetectorVersion
# from app.models.job import Job, JobStatus, JobType      # Task 2
# from app.models.model_registry import (                  # Task 2
#     ModelTransitionLog,
#     ModelVersion,
#     ModelVersionStage,
# )
from app.models.user import Base, Role, User

__all__ = [
    "Base",
    "Role",
    "User",
    "UserGitCredential",
    "Detector",
    "DetectorVersion",
    "DetectorBuild",
    # "DatasetConfig",           # Task 2
    # "Job",
    # "JobStatus",
    # "JobType",
    # "ModelVersion",
    # "ModelVersionStage",
    # "ModelTransitionLog",
]
```

Revised Step 5 (same — keep new lines commented).

Now tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/models/ backend/app/schemas/
git commit -m "feat(backend): phase 4 scaffolding — deps + placeholder model/schema files"
```

---

## Task 2: Phase 4 Data Model + Alembic Migration

**Files:**
- Modify: `backend/app/models/detector.py` (add `mlflow_experiment_id` column)
- Modify: `backend/app/models/dataset.py`
- Modify: `backend/app/models/job.py`
- Modify: `backend/app/models/model_registry.py`
- Create: `backend/alembic/versions/xxx_add_phase4_tables.py` (auto-generated)
- Modify: `backend/app/models/__init__.py` (uncomment imports)
- Modify: `backend/app/schemas/__init__.py` (uncomment imports; keep schemas unwritten, so these fail — Step 9 fixes)

- [ ] **Step 1: Extend detector model with MLflow experiment pointer**

Edit `backend/app/models/detector.py`, add to `DetectorVersion` class:

```python
    mlflow_experiment_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
```

(Place near `config_schema` in the class body.)

- [ ] **Step 2: Write `DatasetConfig` model**

Replace `backend/app/models/dataset.py`:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class DatasetVisibility(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class DatasetConfig(Base):
    __tablename__ = "dataset_config"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    visibility: Mapped[DatasetVisibility] = mapped_column(
        SAEnum(DatasetVisibility, name="dataset_visibility_enum"),
        default=DatasetVisibility.PUBLIC,
        nullable=False,
    )
    csv_content: Mapped[str] = mapped_column(Text, nullable=False)
    csv_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False)
    label_distribution: Mapped[dict] = mapped_column(JSONB, default=dict)
    family_distribution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index(
            "ix_dataset_config_owner_name_unique",
            "owner_id",
            "name",
            unique=True,
            postgresql_where="deleted_at IS NULL",
            sqlite_where=None,
        ),
        Index("ix_dataset_config_owner", "owner_id"),
        Index("ix_dataset_config_visibility", "visibility"),
    )
```

- [ ] **Step 3: Write `Job` model**

Replace `backend/app/models/job.py`:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class JobType(str, enum.Enum):
    TRAIN = "train"
    EVALUATE = "evaluate"
    PREDICT = "predict"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


NON_TERMINAL_STATUSES = {
    JobStatus.PENDING,
    JobStatus.PREPARING,
    JobStatus.RUNNING,
}


class ResourceProfile(str, enum.Enum):
    STANDARD = "standard"
    # Future: CPU_ONLY, HIGH_MEM, MULTI_GPU


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[JobType] = mapped_column(
        SAEnum(JobType, name="job_type_enum"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum"),
        default=JobStatus.PENDING,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    train_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    test_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    predict_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    source_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_version.id"), nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    resolved_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mlflow_experiment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    k8s_job_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resource_profile: Mapped[ResourceProfile] = mapped_column(
        SAEnum(ResourceProfile, name="resource_profile_enum"),
        default=ResourceProfile.STANDARD,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_job_owner_submitted", "owner_id", "submitted_at"),
        Index(
            "ix_job_in_flight",
            "status",
            postgresql_where="status IN ('pending','preparing','running')",
            sqlite_where=None,
        ),
        Index("ix_job_detector_version", "detector_version_id"),
        Index("ix_job_idempotency", "idempotency_key", "submitted_at"),
    )
```

- [ ] **Step 4: Write model-registry models**

Replace `backend/app/models/model_registry.py`:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ModelVersionStage(str, enum.Enum):
    """Mirrors MLflow stages; 'none' = unassigned."""

    NONE = "None"
    STAGING = "Staging"
    PRODUCTION = "Production"
    ARCHIVED = "Archived"


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    mlflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mlflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    current_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum"),
        default=ModelVersionStage.NONE,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    source_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_transitioned_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_model_version_name_version_unique",
            "mlflow_name",
            "mlflow_version",
            unique=True,
        ),
        Index("ix_model_version_owner", "owner_id"),
        Index("ix_model_version_stage", "current_stage"),
    )


class ModelTransitionLog(Base):
    __tablename__ = "model_transition_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id"), nullable=False
    )
    from_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum"), nullable=False
    )
    to_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("ix_model_transition_version", "model_version_id"),
    )
```

- [ ] **Step 5: Uncomment `models/__init__.py` imports**

Re-edit `backend/app/models/__init__.py` to uncomment all the Phase 4 lines from Task 1 Step 4.

- [ ] **Step 6: Generate Alembic migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add phase 4 tables (dataset_config, job, model_version, model_transition_log) + detector_version.mlflow_experiment_id"
```

Review generated file under `backend/alembic/versions/`; verify it includes:
- `CREATE TYPE` for each new enum
- `CREATE TABLE dataset_config`, `job`, `model_version`, `model_transition_log`
- All FK constraints and indexes
- `ALTER TABLE detector_version ADD COLUMN mlflow_experiment_id`

**Common issues to fix manually in the migration:**
- Alembic autogenerate sometimes misses `postgresql_where` clauses on partial indexes. Open the migration file and add them explicitly where needed.
- `SAEnum` reuse (`model_stage_enum` is used by two tables): Alembic may try to `CREATE TYPE` twice. Use `checkfirst=True` on the second usage or keep a single `op.execute("CREATE TYPE ...")` at the top.
- Table creation order: `job` references `model_version`; `model_version` references `job`. This is a circular FK. Break it by:
  1. Create `job` without `source_model_version_id` FK (nullable column is fine)
  2. Create `model_version` (its `source_job_id` FK to `job` now valid)
  3. `op.create_foreign_key('fk_job_source_model_version', 'job', 'model_version', ['source_model_version_id'], ['id'])`

Sample manual adjustment snippet at the bottom of `upgrade()`:

```python
    # Circular FK broken at table-creation time; add the back-reference now
    op.create_foreign_key(
        "fk_job_source_model_version",
        "job",
        "model_version",
        ["source_model_version_id"],
        ["id"],
    )
```

And mirror in `downgrade()` before `op.drop_table`:

```python
    op.drop_constraint("fk_job_source_model_version", "job", type_="foreignkey")
```

- [ ] **Step 7: Apply migration locally (SQLite via tests)**

Tests use aiosqlite; SQLite doesn't support partial indexes the same way. Ensure the migration is SQLite-compatible for tests. If it uses PostgreSQL-specific features, guard them:

```python
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_dataset_config_owner_name_unique",
            "dataset_config",
            ["owner_id", "name"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )
    else:
        op.create_index(
            "ix_dataset_config_owner_name_unique",
            "dataset_config",
            ["owner_id", "name"],
            unique=True,
        )
```

Same for `ix_job_in_flight`.

- [ ] **Step 8: Apply to production DB via kubectl**

The backend deployment runs `alembic upgrade head` on startup (verify this is already set up from Phase 2/3; if not, defer to Task 20). For development, you can `kubectl -n lolday exec deploy/backend -- alembic upgrade head` after deploying the new image. For now, we only need the migration to exist.

- [ ] **Step 9: Verify model definitions load**

```bash
cd backend && uv run python -c "from app.models import DatasetConfig, Job, ModelVersion, ModelTransitionLog; print('ok')"
```

Expected: `ok`

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/ backend/alembic/versions/
git commit -m "feat(backend): add phase 4 data model + alembic migration"
```

---

## Task 3: MLflow Client Service

**Files:**
- Create: `backend/app/services/mlflow_client.py`
- Create: `backend/tests/fixtures/sample_mlflow_responses.py`
- Create: `backend/tests/test_services_mlflow_client.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add MLflow settings**

Edit `backend/app/config.py`, add under Settings class:

```python
    MLFLOW_TRACKING_URI: str = "http://mlflow.lolday.svc:5000"
    MLFLOW_HTTP_TIMEOUT_SECONDS: float = 10.0
    MLFLOW_HTTP_RETRIES: int = 3
```

- [ ] **Step 2: Write fixture MLflow responses**

Create `backend/tests/fixtures/sample_mlflow_responses.py`:

```python
"""Canned MLflow REST responses for respx-based tests.

Source of truth: https://mlflow.org/docs/2.20.0/rest-api.html
"""

EXPERIMENT_CREATED = {
    "experiment_id": "42",
}

EXPERIMENT_GET = {
    "experiment": {
        "experiment_id": "42",
        "name": "detector:upxelfdet:v0.4.0",
        "artifact_location": "file:///mlflow-artifacts/42",
        "lifecycle_stage": "active",
    }
}

RUN_CREATED = {
    "run": {
        "info": {
            "run_id": "abc123def456",
            "experiment_id": "42",
            "status": "RUNNING",
            "start_time": 1713350000000,
            "artifact_uri": "file:///mlflow-artifacts/42/abc123def456/artifacts",
        },
        "data": {"metrics": [], "params": [], "tags": []},
    }
}

RUN_FINISHED = {
    "run": {
        "info": {
            "run_id": "abc123def456",
            "experiment_id": "42",
            "status": "FINISHED",
            "start_time": 1713350000000,
            "end_time": 1713351800000,
            "artifact_uri": "file:///mlflow-artifacts/42/abc123def456/artifacts",
        },
        "data": {
            "metrics": [
                {"key": "accuracy", "value": 0.93, "timestamp": 1713351000000, "step": 0},
                {"key": "f1", "value": 0.91, "timestamp": 1713351000000, "step": 0},
            ],
            "params": [
                {"key": "model.type", "value": "SVM"},
                {"key": "vectorize.method", "value": "ngram_numeric"},
            ],
            "tags": [{"key": "maldet.action", "value": "train"}],
        },
    }
}

MODEL_VERSION_CREATED = {
    "model_version": {
        "name": "upxelfdet",
        "version": "1",
        "creation_timestamp": 1713351900000,
        "last_updated_timestamp": 1713351900000,
        "current_stage": "None",
        "source": "runs:/abc123def456/model",
        "run_id": "abc123def456",
        "status": "READY",
    }
}

MODEL_VERSION_TRANSITIONED = {
    "model_version": {
        "name": "upxelfdet",
        "version": "1",
        "current_stage": "Production",
    }
}

REGISTERED_MODELS_SEARCH = {
    "registered_models": [
        {
            "name": "upxelfdet",
            "creation_timestamp": 1713350000000,
            "last_updated_timestamp": 1713352000000,
            "latest_versions": [
                {"version": "1", "current_stage": "Production", "run_id": "abc123def456"}
            ],
        }
    ]
}

MODEL_VERSIONS_SEARCH = {
    "model_versions": [
        {"name": "upxelfdet", "version": "1", "current_stage": "Production", "run_id": "abc123def456"},
    ]
}
```

- [ ] **Step 3: Write failing test for MLflow client**

Create `backend/tests/test_services_mlflow_client.py`:

```python
import httpx
import pytest
import respx

from app.services.mlflow_client import MlflowClient, MlflowError
from tests.fixtures.sample_mlflow_responses import (
    EXPERIMENT_CREATED,
    EXPERIMENT_GET,
    MODEL_VERSION_CREATED,
    MODEL_VERSION_TRANSITIONED,
    MODEL_VERSIONS_SEARCH,
    REGISTERED_MODELS_SEARCH,
    RUN_CREATED,
    RUN_FINISHED,
)


@pytest.mark.asyncio
@respx.mock
async def test_create_experiment_returns_id():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_CREATED)
    )
    c = MlflowClient("http://mlflow")
    eid = await c.create_experiment("my-exp", artifact_location=None)
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_get_or_create_experiment_reuses_existing():
    """If creating returns 'RESOURCE_ALREADY_EXISTS', fall back to get-by-name."""
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(
            400,
            json={"error_code": "RESOURCE_ALREADY_EXISTS", "message": "experiment exists"},
        )
    )
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_GET)
    )
    c = MlflowClient("http://mlflow")
    eid = await c.get_or_create_experiment("detector:upxelfdet:v0.4.0")
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_create_run_returns_run_id():
    respx.post("http://mlflow/api/2.0/mlflow/runs/create").mock(
        return_value=httpx.Response(200, json=RUN_CREATED)
    )
    c = MlflowClient("http://mlflow")
    rid = await c.create_run("42")
    assert rid == "abc123def456"


@pytest.mark.asyncio
@respx.mock
async def test_get_run_parses_metrics():
    respx.get("http://mlflow/api/2.0/mlflow/runs/get").mock(
        return_value=httpx.Response(200, json=RUN_FINISHED)
    )
    c = MlflowClient("http://mlflow")
    run = await c.get_run("abc123def456")
    assert run["info"]["status"] == "FINISHED"
    assert run["data"]["metrics"][0]["key"] == "accuracy"


@pytest.mark.asyncio
@respx.mock
async def test_create_model_version_returns_version():
    respx.post("http://mlflow/api/2.0/mlflow/model-versions/create").mock(
        return_value=httpx.Response(200, json=MODEL_VERSION_CREATED)
    )
    c = MlflowClient("http://mlflow")
    mv = await c.create_model_version("upxelfdet", "runs:/abc123def456/model", "abc123def456")
    assert mv["version"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_transition_stage_calls_correct_endpoint():
    route = respx.post("http://mlflow/api/2.0/mlflow/model-versions/transition-stage").mock(
        return_value=httpx.Response(200, json=MODEL_VERSION_TRANSITIONED)
    )
    c = MlflowClient("http://mlflow")
    mv = await c.transition_model_version_stage(
        "upxelfdet", "1", "Production", archive_existing_versions=True
    )
    assert route.called
    sent = route.calls.last.request
    body = sent.content.decode("utf-8")
    assert "Production" in body
    assert "archive_existing_versions" in body
    assert mv["current_stage"] == "Production"


@pytest.mark.asyncio
@respx.mock
async def test_search_registered_models_paginates():
    respx.get("http://mlflow/api/2.0/mlflow/registered-models/search").mock(
        return_value=httpx.Response(200, json=REGISTERED_MODELS_SEARCH)
    )
    c = MlflowClient("http://mlflow")
    models = await c.search_registered_models()
    assert models[0]["name"] == "upxelfdet"


@pytest.mark.asyncio
@respx.mock
async def test_search_model_versions():
    respx.post("http://mlflow/api/2.0/mlflow/model-versions/search").mock(
        return_value=httpx.Response(200, json=MODEL_VERSIONS_SEARCH)
    )
    c = MlflowClient("http://mlflow")
    versions = await c.search_model_versions(filter_string="name = 'upxelfdet'")
    assert len(versions) == 1


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_mlflow_error():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(500, json={"error_code": "INTERNAL_ERROR", "message": "boom"})
    )
    c = MlflowClient("http://mlflow")
    with pytest.raises(MlflowError, match="INTERNAL_ERROR"):
        await c.create_experiment("any")


@pytest.mark.asyncio
@respx.mock
async def test_network_timeout_retries_then_raises():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        side_effect=httpx.ConnectError("conn refused")
    )
    c = MlflowClient("http://mlflow", timeout=0.1, retries=2)
    with pytest.raises(MlflowError, match="network"):
        await c.create_experiment("any")
```

- [ ] **Step 4: Run tests to confirm they fail**

```bash
cd backend && uv run pytest tests/test_services_mlflow_client.py -v 2>&1 | tail -15
```

Expected: `ModuleNotFoundError: No module named 'app.services.mlflow_client'`.

- [ ] **Step 5: Implement MLflow client**

Create `backend/app/services/mlflow_client.py`:

```python
import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MlflowError(Exception):
    pass


class MlflowClient:
    """Async thin REST wrapper for MLflow Tracking + Model Registry.

    We don't import mlflow-skinny's own client because it's sync; we reuse httpx
    for backend-wide async consistency. Endpoints per MLflow 2.20 REST API.
    """

    def __init__(
        self,
        tracking_uri: str,
        timeout: float = 10.0,
        retries: int = 3,
    ) -> None:
        self._base = tracking_uri.rstrip("/")
        self._timeout = httpx.Timeout(timeout)
        self._retries = retries

    # ----------- helpers -----------

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self._base}/api/2.0/mlflow{path}"
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(method, url, json=json, params=params)
                if resp.status_code >= 400:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {"error_code": "UNKNOWN", "message": resp.text}
                    return self._handle_error(resp.status_code, body)
                return resp.json() if resp.content else {}
            except httpx.HTTPError as e:
                last_exc = e
                await asyncio.sleep(0.2 * (attempt + 1))
        raise MlflowError(f"network error after {self._retries} retries: {last_exc!r}")

    def _handle_error(self, status: int, body: dict) -> dict:
        code = body.get("error_code", "UNKNOWN")
        msg = body.get("message", "")
        # RESOURCE_ALREADY_EXISTS / RESOURCE_DOES_NOT_EXIST are signaled via exception;
        # caller handles by catching MlflowError and inspecting .code
        e = MlflowError(f"{code}: {msg}")
        e.code = code  # type: ignore[attr-defined]
        e.http_status = status  # type: ignore[attr-defined]
        raise e

    # ----------- experiments -----------

    async def create_experiment(
        self, name: str, artifact_location: str | None = None
    ) -> str:
        payload: dict[str, Any] = {"name": name}
        if artifact_location:
            payload["artifact_location"] = artifact_location
        resp = await self._request("POST", "/experiments/create", json=payload)
        return resp["experiment_id"]

    async def get_experiment_by_name(self, name: str) -> dict[str, Any]:
        resp = await self._request("GET", "/experiments/get-by-name", params={"experiment_name": name})
        return resp["experiment"]

    async def get_or_create_experiment(
        self, name: str, artifact_location: str | None = None
    ) -> str:
        try:
            return await self.create_experiment(name, artifact_location)
        except MlflowError as e:
            if getattr(e, "code", "") == "RESOURCE_ALREADY_EXISTS":
                exp = await self.get_experiment_by_name(name)
                return exp["experiment_id"]
            raise

    async def search_experiments(self, max_results: int = 100) -> list[dict[str, Any]]:
        resp = await self._request(
            "POST", "/experiments/search", json={"max_results": max_results}
        )
        return resp.get("experiments", [])

    # ----------- runs -----------

    async def create_run(
        self, experiment_id: str, tags: list[dict[str, str]] | None = None
    ) -> str:
        payload: dict[str, Any] = {"experiment_id": experiment_id}
        if tags:
            payload["tags"] = tags
        resp = await self._request("POST", "/runs/create", json=payload)
        return resp["run"]["info"]["run_id"]

    async def get_run(self, run_id: str) -> dict[str, Any]:
        resp = await self._request("GET", "/runs/get", params={"run_id": run_id})
        return resp["run"]

    async def search_runs(
        self,
        experiment_ids: list[str],
        filter_string: str | None = None,
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "experiment_ids": experiment_ids,
            "max_results": max_results,
        }
        if filter_string:
            payload["filter"] = filter_string
        resp = await self._request("POST", "/runs/search", json=payload)
        return resp.get("runs", [])

    async def update_run(
        self,
        run_id: str,
        status: str | None = None,
        end_time: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"run_id": run_id}
        if status:
            payload["status"] = status
        if end_time:
            payload["end_time"] = end_time
        await self._request("POST", "/runs/update", json=payload)

    async def set_run_tag(self, run_id: str, key: str, value: str) -> None:
        await self._request(
            "POST", "/runs/set-tag", json={"run_id": run_id, "key": key, "value": value}
        )

    # ----------- model registry -----------

    async def create_model_version(
        self, name: str, source: str, run_id: str
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/model-versions/create",
            json={"name": name, "source": source, "run_id": run_id},
        )
        return resp["model_version"]

    async def transition_model_version_stage(
        self,
        name: str,
        version: str,
        stage: str,
        archive_existing_versions: bool = False,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/model-versions/transition-stage",
            json={
                "name": name,
                "version": str(version),
                "stage": stage,
                "archive_existing_versions": archive_existing_versions,
            },
        )
        return resp["model_version"]

    async def delete_model_version(self, name: str, version: str) -> None:
        await self._request(
            "DELETE", "/model-versions/delete", json={"name": name, "version": str(version)}
        )

    async def search_registered_models(self, max_results: int = 100) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            "/registered-models/search",
            params={"max_results": max_results},
        )
        return resp.get("registered_models", [])

    async def search_model_versions(
        self,
        filter_string: str | None = None,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"max_results": max_results}
        if filter_string:
            payload["filter"] = filter_string
        resp = await self._request("POST", "/model-versions/search", json=payload)
        return resp.get("model_versions", [])

    async def create_registered_model(self, name: str) -> dict[str, Any]:
        try:
            resp = await self._request(
                "POST", "/registered-models/create", json={"name": name}
            )
            return resp["registered_model"]
        except MlflowError as e:
            if getattr(e, "code", "") == "RESOURCE_ALREADY_EXISTS":
                return {"name": name}
            raise
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd backend && uv run pytest tests/test_services_mlflow_client.py -v 2>&1 | tail -20
```

Expected: all 10 tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mlflow_client.py backend/app/config.py backend/tests/
git commit -m "feat(backend): add async MLflow REST client service"
```

---

## Task 4: Dataset Service — CSV Parsing, Checksum, Sample Integrity

**Files:**
- Create: `backend/app/services/dataset.py`
- Create: `backend/tests/fixtures/sample_dataset.csv`
- Create: `backend/tests/test_services_dataset.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add dataset settings**

Edit `backend/app/config.py`, add under Settings class:

```python
    DATASET_CSV_MAX_BYTES: int = 10 * 1024 * 1024            # 10 MiB
    DATASET_SPOT_CHECK_COUNT: int = 100                       # files per job dispatch
    DATASET_SPOT_CHECK_MISSING_THRESHOLD: int = 1             # fail if >= this many missing
    SAMPLES_ROOT: str = "/mnt/samples"                        # parent of malware/, benign/
    SAMPLES_LOCAL_ROOT: str = "/data"                         # for backend-side validation (matches hostPath)
```

- [ ] **Step 2: Create fixture CSV**

Create `backend/tests/fixtures/sample_dataset.csv`:

```
file_name,label,family,md5,size,is_packed
0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11,Malware,xorddos,6a1280e0e5f5ca168d3aa7e422819dc7,548693,False
0000002a10959ec38b808d8252eed2e814294fbb25d2cd016b24bf853a44857e,Malware,gafgyt,317228475fed0e69ddb8f8c62a7db890,104139,False
00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35,Malware,xorddos,e396ff8eec04db9c1ba1eeb3734807fc,253353,True
00000ef0e4f972c11260234c9e8308ef67883828a39d42df9a880d5ccbdedfc2,Malware,xorddos,7ee5d2cff373af0a658e73a53a225d3e,253353,True
00001167300f0d583aff72a78a99a84a0729f3d159e03fc52a0ae926906b306c,Malware,ngioweb,9961981cacc112f39c0115042582f949,96710,False
deadbeef0000000000000000000000000000000000000000000000000000beef,Benign,,d41d8cd98f00b204e9800998ecf8427e,1024,False
cafebabe0000000000000000000000000000000000000000000000000000babe,Benign,,b026324c6904b2a9cb4b88d6d61c81d1,2048,False
```

7 rows: 5 Malware, 2 Benign. File names are 64-char SHA256 hex. Includes `family` for malware (missing/empty for benign), `md5`, `size`, `is_packed`.

- [ ] **Step 3: Write failing tests**

Create `backend/tests/test_services_dataset.py`:

```python
import hashlib
import os
import shutil
from pathlib import Path

import pytest

from app.services.dataset import (
    DatasetIntegrityError,
    DatasetValidationError,
    compute_checksum,
    parse_csv,
    spot_check_samples,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dataset.csv"


def test_parse_valid_csv_returns_stats():
    content = FIXTURE.read_text()
    result = parse_csv(content)

    assert result.sample_count == 7
    assert result.label_distribution == {"Malware": 5, "Benign": 2}
    assert result.family_distribution == {"xorddos": 3, "gafgyt": 1, "ngioweb": 1}
    assert result.size_bytes == len(content.encode("utf-8"))


def test_parse_csv_missing_file_name_column_raises():
    bad = "label,family\nMalware,xorddos\n"
    with pytest.raises(DatasetValidationError, match="file_name"):
        parse_csv(bad)


def test_parse_csv_missing_label_column_raises():
    bad = "file_name\n0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11\n"
    with pytest.raises(DatasetValidationError, match="label"):
        parse_csv(bad)


def test_parse_csv_rejects_non_sha256_filename():
    bad = (
        "file_name,label\n"
        "not-a-sha256,Malware\n"
    )
    with pytest.raises(DatasetValidationError, match="file_name"):
        parse_csv(bad)


def test_parse_csv_rejects_uppercase_hex():
    bad = (
        "file_name,label\n"
        "0000002158D35C2BB5E7D96A39FF464EA4C83DE8C5FD72094736F79125AACA11,Malware\n"
    )
    with pytest.raises(DatasetValidationError, match="lowercase"):
        parse_csv(bad)


def test_parse_empty_csv_rejected():
    with pytest.raises(DatasetValidationError, match="empty"):
        parse_csv("file_name,label\n")


def test_parse_malformed_csv_rejected():
    bad = "file_name,label\nabc\n"  # too few columns
    with pytest.raises(DatasetValidationError):
        parse_csv(bad)


def test_compute_checksum_is_sha256_of_bytes():
    content = "hello"
    expected = hashlib.sha256(b"hello").hexdigest()
    assert compute_checksum(content) == expected


def test_spot_check_all_present(tmp_path):
    samples_root = tmp_path / "samples"
    (samples_root / "malware" / "00").mkdir(parents=True)
    name = "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11"
    (samples_root / "malware" / "00" / name).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[name],
        labels=["Malware"],
        samples_root=samples_root,
        sample_count=1,
        missing_threshold=1,
    )
    assert result.checked == 1
    assert result.missing == 0


def test_spot_check_all_missing(tmp_path):
    samples_root = tmp_path / "samples"
    (samples_root / "malware").mkdir(parents=True)

    names = [
        "0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11",
        "00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35",
    ]
    labels = ["Malware", "Malware"]
    with pytest.raises(DatasetIntegrityError, match="2 missing"):
        spot_check_samples(
            file_names=names,
            labels=labels,
            samples_root=samples_root,
            sample_count=2,
            missing_threshold=1,
        )


def test_spot_check_benign_uses_benign_folder(tmp_path):
    samples_root = tmp_path / "samples"
    (samples_root / "benign" / "de").mkdir(parents=True)
    benign = "deadbeef0000000000000000000000000000000000000000000000000000beef"
    (samples_root / "benign" / "de" / benign).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[benign],
        labels=["Benign"],
        samples_root=samples_root,
        sample_count=1,
        missing_threshold=1,
    )
    assert result.missing == 0


def test_spot_check_sample_count_exceeds_dataset(tmp_path):
    """When sample_count > len(file_names), check all of them."""
    samples_root = tmp_path / "samples"
    (samples_root / "malware" / "aa").mkdir(parents=True)
    name = "aa" + "0" * 62
    (samples_root / "malware" / "aa" / name).write_bytes(b"fake")

    result = spot_check_samples(
        file_names=[name],
        labels=["Malware"],
        samples_root=samples_root,
        sample_count=9999,
        missing_threshold=1,
    )
    assert result.checked == 1
    assert result.missing == 0


def test_spot_check_rejects_unknown_label(tmp_path):
    samples_root = tmp_path / "samples"
    names = ["0" * 64]
    labels = ["Weird"]
    with pytest.raises(DatasetValidationError, match="label"):
        spot_check_samples(
            file_names=names,
            labels=labels,
            samples_root=samples_root,
            sample_count=1,
            missing_threshold=1,
        )
```

- [ ] **Step 4: Run tests, confirm they fail**

```bash
cd backend && uv run pytest tests/test_services_dataset.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'app.services.dataset'`.

- [ ] **Step 5: Implement dataset service**

Create `backend/app/services/dataset.py`:

```python
"""Dataset CSV parsing + integrity validation.

- parse_csv: validates format, computes label/family/sample distributions + checksum
- compute_checksum: SHA256 of raw CSV bytes (UTF-8)
- spot_check_samples: randomly picks N file_names and verifies they exist on disk

Design notes:
- File name convention: 64-char lowercase hex (SHA256)
- Samples live under <samples_root>/{malware,benign}/<first_2_chars>/<file_name>
- `label` column values: "Malware" (case-sensitive, matches CSV fixture) or "Benign"
- Full existence scan is O(N); spot-check is cheap and catches catastrophic mount failures
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import re
from dataclasses import dataclass
from pathlib import Path


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VALID_LABELS = {"Malware", "Benign"}


class DatasetValidationError(ValueError):
    """Raised on malformed CSV / bad values."""


class DatasetIntegrityError(RuntimeError):
    """Raised when spot-check finds ≥ threshold missing samples."""


@dataclass(frozen=True)
class ParsedCsv:
    sample_count: int
    label_distribution: dict[str, int]
    family_distribution: dict[str, int] | None
    size_bytes: int
    checksum: str
    file_names: list[str]
    labels: list[str]


@dataclass(frozen=True)
class SpotCheckResult:
    checked: int
    missing: int


def compute_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_csv(content: str) -> ParsedCsv:
    if not content or not content.strip():
        raise DatasetValidationError("CSV is empty")

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise DatasetValidationError("CSV has no header")

    required = {"file_name", "label"}
    missing_cols = required - set(reader.fieldnames)
    if missing_cols:
        raise DatasetValidationError(f"CSV missing columns: {sorted(missing_cols)}")

    has_family_col = "family" in reader.fieldnames

    file_names: list[str] = []
    labels: list[str] = []
    label_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}

    for row_num, row in enumerate(reader, start=2):  # start=2 accounts for header row
        name = (row.get("file_name") or "").strip()
        label = (row.get("label") or "").strip()
        if not name:
            raise DatasetValidationError(f"row {row_num}: empty file_name")
        if not SHA256_PATTERN.match(name):
            if name.lower() == name:
                raise DatasetValidationError(
                    f"row {row_num}: file_name must be 64-char lowercase hex (SHA256), got: {name!r}"
                )
            raise DatasetValidationError(
                f"row {row_num}: file_name must be lowercase hex: {name!r}"
            )
        if label not in VALID_LABELS:
            raise DatasetValidationError(
                f"row {row_num}: label must be one of {sorted(VALID_LABELS)}, got: {label!r}"
            )

        file_names.append(name)
        labels.append(label)
        label_counts[label] = label_counts.get(label, 0) + 1

        if has_family_col and label == "Malware":
            family = (row.get("family") or "").strip()
            if family:
                family_counts[family] = family_counts.get(family, 0) + 1

    if not file_names:
        raise DatasetValidationError("CSV is empty (no data rows)")

    return ParsedCsv(
        sample_count=len(file_names),
        label_distribution=label_counts,
        family_distribution=family_counts if family_counts else None,
        size_bytes=len(content.encode("utf-8")),
        checksum=compute_checksum(content),
        file_names=file_names,
        labels=labels,
    )


def _sample_path(samples_root: Path, label: str, file_name: str) -> Path:
    subdir = "malware" if label == "Malware" else "benign"
    prefix = file_name[:2]
    return samples_root / subdir / prefix / file_name


def spot_check_samples(
    *,
    file_names: list[str],
    labels: list[str],
    samples_root: Path,
    sample_count: int,
    missing_threshold: int,
    rng: random.Random | None = None,
) -> SpotCheckResult:
    """Verify random subset of samples exists on disk.

    Args:
      file_names: parallel list of SHA256 file names
      labels: parallel list of labels (values: Malware | Benign)
      samples_root: mount root containing malware/ and benign/ subfolders
      sample_count: how many samples to check; clamped to len(file_names)
      missing_threshold: raise DatasetIntegrityError if missing >= threshold

    Returns:
      SpotCheckResult on success (missing < threshold)

    Raises:
      DatasetValidationError: label not recognised
      DatasetIntegrityError: too many samples missing
    """
    if len(file_names) != len(labels):
        raise DatasetValidationError("file_names and labels length mismatch")
    for lbl in labels:
        if lbl not in VALID_LABELS:
            raise DatasetValidationError(f"unexpected label: {lbl!r}")

    n = min(sample_count, len(file_names))
    indices = list(range(len(file_names)))
    rng = rng or random.Random()
    rng.shuffle(indices)
    indices = indices[:n]

    missing = 0
    for i in indices:
        p = _sample_path(samples_root, labels[i], file_names[i])
        if not p.exists():
            missing += 1

    if missing >= missing_threshold:
        raise DatasetIntegrityError(
            f"spot-check: {missing} missing out of {n} (threshold {missing_threshold})"
        )

    return SpotCheckResult(checked=n, missing=missing)
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd backend && uv run pytest tests/test_services_dataset.py -v 2>&1 | tail -20
```

Expected: all 13 tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/dataset.py backend/app/config.py backend/tests/
git commit -m "feat(backend): add dataset CSV parser + spot-check service"
```

---

## Task 5: Dataset Schemas + Router (CRUD)

**Files:**
- Modify: `backend/app/schemas/dataset.py`
- Create: `backend/app/routers/datasets.py`
- Create: `backend/tests/test_datasets.py`
- Modify: `backend/app/schemas/__init__.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write Pydantic schemas**

Replace `backend/app/schemas/dataset.py`:

```python
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.dataset import DatasetVisibility


class DatasetConfigCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: str | None = None
    visibility: DatasetVisibility = DatasetVisibility.PUBLIC
    csv_content: Annotated[str, Field(min_length=1)]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty or whitespace-only")
        return v


class DatasetConfigUpdate(BaseModel):
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    description: str | None = None
    visibility: DatasetVisibility | None = None


class DatasetConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    visibility: DatasetVisibility
    sample_count: int
    label_distribution: dict
    family_distribution: dict | None
    size_bytes: int
    csv_checksum: str
    created_at: datetime


class DatasetConfigList(BaseModel):
    items: list[DatasetConfigRead]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 2: Uncomment schema exports**

Edit `backend/app/schemas/__init__.py`, uncomment the dataset-related lines from Task 1:

```python
from app.schemas.dataset import (
    DatasetConfigCreate,
    DatasetConfigRead,
    DatasetConfigUpdate,
)
```

And in `__all__`:

```python
__all__ = [
    # ... existing ...
    "DatasetConfigCreate",
    "DatasetConfigRead",
    "DatasetConfigUpdate",
]
```

- [ ] **Step 3: Write failing router tests**

Create `backend/tests/test_datasets.py`:

```python
from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()


@pytest.mark.asyncio
async def test_create_dataset_happy_path(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "test-upx-ds",
            "description": "7-row fixture",
            "visibility": "public",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sample_count"] == 7
    assert body["label_distribution"] == {"Malware": 5, "Benign": 2}
    assert body["csv_checksum"]
    assert "csv_content" not in body  # don't leak content in GET/POST response


@pytest.mark.asyncio
async def test_create_dataset_rejects_oversize(user_client: AsyncClient, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DATASET_CSV_MAX_BYTES", 10)
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "big", "csv_content": FIXTURE_CSV},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_create_dataset_rejects_malformed_csv(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "bad", "csv_content": "not,a,valid,csv\n"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_rejected(user_client: AsyncClient):
    await user_client.post(
        "/api/v1/datasets",
        json={"name": "dup", "csv_content": FIXTURE_CSV},
    )
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "dup", "csv_content": FIXTURE_CSV},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_datasets_paginated(user_client: AsyncClient):
    for i in range(3):
        await user_client.post(
            "/api/v1/datasets",
            json={"name": f"d{i}", "csv_content": FIXTURE_CSV},
        )
    r = await user_client.get("/api/v1/datasets?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_get_dataset_returns_metadata_not_content(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r.status_code == 200
    assert "csv_content" not in r.json()


@pytest.mark.asyncio
async def test_get_dataset_csv_returns_raw_content(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text == FIXTURE_CSV


@pytest.mark.asyncio
async def test_private_dataset_hidden_from_other_user(
    user_client: AsyncClient, second_user_client: AsyncClient
):
    r1 = await user_client.post(
        "/api/v1/datasets",
        json={"name": "secret", "visibility": "private", "csv_content": FIXTURE_CSV},
    )
    ds_id = r1.json()["id"]

    r2 = await second_user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_patch_dataset_only_allowed_fields(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]

    # description can be changed
    r = await user_client.patch(
        f"/api/v1/datasets/{ds_id}",
        json={"description": "new desc"},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "new desc"

    # csv_content cannot (not in schema)
    r2 = await user_client.patch(
        f"/api/v1/datasets/{ds_id}",
        json={"csv_content": "x"},
    )
    # FastAPI silently drops unknown fields in schema; we verify the content wasn't changed:
    get_r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert get_r.text == FIXTURE_CSV


@pytest.mark.asyncio
async def test_clone_dataset_makes_copy_owned_by_caller(
    user_client: AsyncClient, second_user_client: AsyncClient
):
    r1 = await user_client.post(
        "/api/v1/datasets",
        json={"name": "orig", "csv_content": FIXTURE_CSV},
    )
    orig_id = r1.json()["id"]
    orig_owner = r1.json()["owner_id"]

    r2 = await second_user_client.post(f"/api/v1/datasets/{orig_id}/clone")
    assert r2.status_code == 201
    body = r2.json()
    assert body["owner_id"] != orig_owner
    assert body["name"].endswith("-clone")
    assert body["csv_checksum"] == r1.json()["csv_checksum"]


@pytest.mark.asyncio
async def test_delete_dataset_soft_deletes(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "to-del", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]

    r = await user_client.delete(f"/api/v1/datasets/{ds_id}")
    assert r.status_code == 204

    r2 = await user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_dataset_blocked_by_active_job(
    user_client: AsyncClient, user_with_active_job_on
):
    ds_id = await user_with_active_job_on()
    r = await user_client.delete(f"/api/v1/datasets/{ds_id}")
    assert r.status_code == 409
```

The fixture `user_with_active_job_on` will be added in Task 9 when jobs exist. For now, mark that test as `@pytest.mark.xfail(reason="depends on Task 9")` to keep the suite green:

```python
@pytest.mark.asyncio
@pytest.mark.xfail(reason="depends on Task 9 job creation")
async def test_delete_dataset_blocked_by_active_job(user_client: AsyncClient):
    ...
```

Also need `second_user_client` fixture. Add to `backend/tests/conftest.py`:

```python
@pytest.fixture
async def second_user_client(client_factory):
    """Distinct authenticated client, different user from `user_client`."""
    async with client_factory(email="second@example.com", password="pass1234") as c:
        yield c
```

Assumes `client_factory` exists from Phase 2/3; if not, reuse existing helper patterns.

- [ ] **Step 4: Run tests, confirm they fail**

```bash
cd backend && uv run pytest tests/test_datasets.py -v 2>&1 | tail -15
```

Expected: `ModuleNotFoundError` or 404 on all endpoints.

- [ ] **Step 5: Implement dataset router**

Create `backend/app/routers/datasets.py`:

```python
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.deps import current_active_user
from app.models import DatasetConfig, Job, User
from app.models.dataset import DatasetVisibility
from app.models.job import NON_TERMINAL_STATUSES
from app.schemas.dataset import (
    DatasetConfigCreate,
    DatasetConfigList,
    DatasetConfigRead,
    DatasetConfigUpdate,
)
from app.services.dataset import DatasetValidationError, parse_csv

router = APIRouter()


async def _get_readable_dataset(
    ds_id: uuid.UUID, session: AsyncSession, user: User
) -> DatasetConfig:
    ds = await session.get(DatasetConfig, ds_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if ds.visibility == DatasetVisibility.PRIVATE and ds.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="dataset not found")
    return ds


async def _get_writable_dataset(
    ds_id: uuid.UUID, session: AsyncSession, user: User
) -> DatasetConfig:
    ds = await _get_readable_dataset(ds_id, session, user)
    if ds.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    return ds


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DatasetConfigRead,
)
async def create_dataset(
    body: DatasetConfigCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    if len(body.csv_content.encode("utf-8")) > settings.DATASET_CSV_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds {settings.DATASET_CSV_MAX_BYTES} bytes",
        )

    try:
        parsed = parse_csv(body.csv_content)
    except DatasetValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Uniqueness check (per-owner + non-deleted)
    stmt = select(DatasetConfig).where(
        DatasetConfig.owner_id == user.id,
        DatasetConfig.name == body.name,
        DatasetConfig.deleted_at.is_(None),
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"dataset name '{body.name}' already in use")

    ds = DatasetConfig(
        name=body.name,
        description=body.description,
        owner_id=user.id,
        visibility=body.visibility,
        csv_content=body.csv_content,
        csv_checksum=parsed.checksum,
        sample_count=parsed.sample_count,
        label_distribution=parsed.label_distribution,
        family_distribution=parsed.family_distribution,
        size_bytes=parsed.size_bytes,
    )
    session.add(ds)
    await session.commit()
    await session.refresh(ds)
    return DatasetConfigRead.model_validate(ds)


@router.get("", response_model=DatasetConfigList)
async def list_datasets(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    owner_id: uuid.UUID | None = None,
    visibility: DatasetVisibility | None = None,
    search: str | None = None,
) -> DatasetConfigList:
    filters = [DatasetConfig.deleted_at.is_(None)]

    # visibility filter: public OR owned-by-user
    visibility_filter = or_(
        DatasetConfig.visibility == DatasetVisibility.PUBLIC,
        DatasetConfig.owner_id == user.id,
    )
    if user.role.value == "admin":
        visibility_filter = True  # admin sees all
    filters.append(visibility_filter)

    if owner_id is not None:
        filters.append(DatasetConfig.owner_id == owner_id)
    if visibility is not None:
        filters.append(DatasetConfig.visibility == visibility)
    if search:
        filters.append(DatasetConfig.name.ilike(f"%{search}%"))

    count_stmt = select(func.count()).select_from(DatasetConfig).where(and_(*filters))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        select(DatasetConfig)
        .where(and_(*filters))
        .order_by(DatasetConfig.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await session.execute(stmt)).scalars().all()

    return DatasetConfigList(
        items=[DatasetConfigRead.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{ds_id}", response_model=DatasetConfigRead)
async def get_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    ds = await _get_readable_dataset(ds_id, session, user)
    return DatasetConfigRead.model_validate(ds)


@router.get("/{ds_id}/csv")
async def get_dataset_csv(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    ds = await _get_readable_dataset(ds_id, session, user)
    return Response(
        content=ds.csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{ds.name}.csv"'},
    )


@router.patch("/{ds_id}", response_model=DatasetConfigRead)
async def update_dataset(
    ds_id: uuid.UUID,
    body: DatasetConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    ds = await _get_writable_dataset(ds_id, session, user)

    if body.name is not None and body.name != ds.name:
        # ensure no collision
        stmt = select(DatasetConfig).where(
            DatasetConfig.owner_id == ds.owner_id,
            DatasetConfig.name == body.name,
            DatasetConfig.deleted_at.is_(None),
            DatasetConfig.id != ds.id,
        )
        if (await session.execute(stmt)).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="name in use")
        ds.name = body.name
    if body.description is not None:
        ds.description = body.description
    if body.visibility is not None:
        ds.visibility = body.visibility

    await session.commit()
    await session.refresh(ds)
    return DatasetConfigRead.model_validate(ds)


@router.post(
    "/{ds_id}/clone",
    status_code=status.HTTP_201_CREATED,
    response_model=DatasetConfigRead,
)
async def clone_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    orig = await _get_readable_dataset(ds_id, session, user)

    # Make a name that doesn't collide with caller's existing datasets
    base = f"{orig.name}-clone"
    new_name = base
    suffix = 2
    while True:
        stmt = select(DatasetConfig).where(
            DatasetConfig.owner_id == user.id,
            DatasetConfig.name == new_name,
            DatasetConfig.deleted_at.is_(None),
        )
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            break
        new_name = f"{base}-{suffix}"
        suffix += 1

    copy = DatasetConfig(
        name=new_name,
        description=orig.description,
        owner_id=user.id,
        visibility=DatasetVisibility.PUBLIC,   # clones default to public; owner can flip later
        csv_content=orig.csv_content,
        csv_checksum=orig.csv_checksum,
        sample_count=orig.sample_count,
        label_distribution=orig.label_distribution,
        family_distribution=orig.family_distribution,
        size_bytes=orig.size_bytes,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return DatasetConfigRead.model_validate(copy)


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    ds = await _get_writable_dataset(ds_id, session, user)

    # Block if any non-terminal job references this dataset in any slot
    stmt = select(func.count()).select_from(Job).where(
        Job.status.in_(NON_TERMINAL_STATUSES),
        or_(
            Job.train_dataset_id == ds.id,
            Job.test_dataset_id == ds.id,
            Job.predict_dataset_id == ds.id,
        ),
    )
    in_flight = (await session.execute(stmt)).scalar_one()
    if in_flight > 0:
        raise HTTPException(status_code=409, detail=f"{in_flight} in-flight job(s) reference this dataset")

    ds.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 6: Register router in main.py**

Edit `backend/app/main.py`, add import and include_router:

```python
from app.routers import admin, credentials, datasets, detectors, internal
```

And after the existing `include_router` calls:

```python
app.include_router(
    datasets.router,
    prefix="/api/v1/datasets",
    tags=["datasets"],
)
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
cd backend && uv run pytest tests/test_datasets.py -v 2>&1 | tail -25
```

Expected: all tests pass (except the xfail'd one).

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/datasets.py backend/app/schemas/ backend/app/main.py backend/tests/
git commit -m "feat(backend): add dataset CRUD endpoints"
```

---

## Task 6: Job Token Service + Config Rendering

**Files:**
- Create: `backend/app/services/job_tokens.py`
- Create: `backend/app/services/job_config.py`
- Create: `backend/tests/test_services_job_tokens.py`
- Create: `backend/tests/test_services_job_config.py`

- [ ] **Step 1: Write failing job_tokens tests**

Create `backend/tests/test_services_job_tokens.py`:

```python
from app.services.job_tokens import generate_token, hash_token, verify_token


def test_generate_token_is_unique():
    t1 = generate_token()
    t2 = generate_token()
    assert t1 != t2
    assert len(t1) >= 32  # urlsafe base64 of 32 bytes is ~43


def test_hash_token_deterministic():
    t = "my-token"
    h1 = hash_token(t)
    h2 = hash_token(t)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_verify_token_matches():
    t = generate_token()
    h = hash_token(t)
    assert verify_token(t, h) is True


def test_verify_token_rejects_wrong():
    t = generate_token()
    h = hash_token(t)
    assert verify_token("other-token", h) is False
```

- [ ] **Step 2: Confirm failing**

```bash
cd backend && uv run pytest tests/test_services_job_tokens.py -v 2>&1 | tail -10
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement job_tokens.py**

Create `backend/app/services/job_tokens.py`:

```python
"""Job-scoped one-time tokens.

Used by init containers to authenticate back to the backend for config/CSV fetch.
Raw token lives in a K8s Secret injected into the init container; the backend
stores only the SHA256 hash in the DB. Secret is deleted on job finalize.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token() -> str:
    """Return URL-safe base64 token of 32 random bytes (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA256 hex digest of the token bytes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, stored_hash: str) -> bool:
    """Constant-time comparison of hash(token) against stored_hash."""
    return hmac.compare_digest(hash_token(token), stored_hash)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_services_job_tokens.py -v 2>&1 | tail -10
```

Expected: 4 pass.

- [ ] **Step 5: Write failing job_config tests**

Create `backend/tests/test_services_job_config.py`:

```python
import pytest

from app.services.job_config import (
    JobConfigRenderer,
    compute_idempotency_key,
    resolve_source_model_path,
)


def test_compute_idempotency_key_stable_across_dict_order():
    p1 = {"model": {"C": 1, "kernel": "rbf"}, "seed": 8017}
    p2 = {"seed": 8017, "model": {"kernel": "rbf", "C": 1}}
    k1 = compute_idempotency_key(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None, params=p1,
    )
    k2 = compute_idempotency_key(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None, params=p2,
    )
    assert k1 == k2


def test_compute_idempotency_key_differs_on_params():
    base_args = dict(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None,
    )
    k1 = compute_idempotency_key(**base_args, params={"C": 1})
    k2 = compute_idempotency_key(**base_args, params={"C": 10})
    assert k1 != k2


def test_render_train_config_injects_standard_paths():
    detector_defaults = {
        "data": {"train": "./train.csv", "test": "./test.csv", "dataset": "./data"},
        "output": {"model": "./model", "feature": "./feat", "vectorize": "./vec",
                   "prediction": "./pred.csv", "log": "./log"},
        "feature": {"section_name": ".block_1"},
        "vectorize": {"method": "ngram_numeric", "ngram_size": 2, "encoding": "TF"},
        "model": {"type": "SVM", "params": {"C": 100, "kernel": "rbf"}},
        "classify": True,
        "seed": 8017,
    }
    user_params = {
        "model": {"params": {"C": 50}},   # deep-merged
        "seed": 42,
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="train",
        detector_defaults=detector_defaults,
        user_params=user_params,
    )
    assert cfg["data"]["train"] == "/mnt/config/train.csv"
    assert cfg["data"]["test"] == "/mnt/config/test.csv"
    assert cfg["data"]["dataset"] == "/mnt/samples"
    assert cfg["output"]["model"] == "/mnt/output/model"
    assert cfg["output"]["prediction"] == "/mnt/output/prediction.csv"
    assert cfg["seed"] == 42
    assert cfg["model"]["params"]["C"] == 50
    assert cfg["model"]["params"]["kernel"] == "rbf"  # preserved from defaults
    assert cfg["model"]["type"] == "SVM"


def test_render_eval_config_points_model_at_source():
    detector_defaults = {
        "data": {"test": "./test.csv", "dataset": "./data"},
        "output": {"model": "./model", "prediction": "./pred.csv", "log": "./log"},
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="evaluate",
        detector_defaults=detector_defaults,
        user_params={},
    )
    # For evaluate: output.model is overridden to source-model (loaded by init)
    assert cfg["output"]["model"] == "/mnt/source-model"
    assert cfg["data"]["test"] == "/mnt/config/test.csv"


def test_render_predict_config():
    detector_defaults = {
        "data": {"predict": "./predict.csv", "dataset": "./data"},
        "output": {"model": "./model", "prediction": "./pred.csv", "log": "./log"},
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="predict",
        detector_defaults=detector_defaults,
        user_params={},
    )
    assert cfg["data"]["predict"] == "/mnt/config/predict.csv"
    assert cfg["output"]["model"] == "/mnt/source-model"
    assert cfg["output"]["prediction"] == "/mnt/output/prediction.csv"


def test_resolve_source_model_path():
    assert resolve_source_model_path("runs:/abc/model") == "model"
    assert resolve_source_model_path("runs:/abc123/model/subdir") == "model/subdir"
```

- [ ] **Step 6: Confirm failing**

```bash
cd backend && uv run pytest tests/test_services_job_config.py -v 2>&1 | tail -10
```

Expected: ModuleNotFoundError.

- [ ] **Step 7: Implement job_config.py**

Create `backend/app/services/job_config.py`:

```python
"""Job config rendering + idempotency utilities.

`resolved_config` is the exact JSON that will be written to /mnt/config/config.json
inside the detector container. This module is the single source of truth for that
shape.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any


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
    """Deterministic SHA256 over all submission identity inputs.

    Dict ordering is normalized via json.dumps(sort_keys=True) so param key
    order doesn't produce different keys.
    """
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
    """Recursive merge: dicts merge, non-dict values from src override dst."""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k] = _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


@dataclass(frozen=True)
class JobConfigRenderer:
    """Encapsulates the mount-path contract between backend and job pod.

    Paths are documented in spec §Job Pod Specification.
    """

    samples_root: str                   # e.g. /mnt/samples
    config_mount: str                   # e.g. /mnt/config
    output_mount: str                   # e.g. /mnt/output
    source_model_mount: str             # e.g. /mnt/source-model

    def render(
        self,
        *,
        job_type: str,
        detector_defaults: dict[str, Any],
        user_params: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = copy.deepcopy(detector_defaults)

        # Deep-merge user overrides (user cannot override paths — those are re-injected below)
        cfg = _deep_merge(cfg, copy.deepcopy(user_params))

        cfg.setdefault("data", {})
        cfg.setdefault("output", {})

        cfg["data"]["dataset"] = self.samples_root
        cfg["data"]["train"] = f"{self.config_mount}/train.csv"
        cfg["data"]["test"] = f"{self.config_mount}/test.csv"
        cfg["data"]["predict"] = f"{self.config_mount}/predict.csv"

        cfg["output"]["feature"] = f"{self.output_mount}/features"
        cfg["output"]["vectorize"] = f"{self.output_mount}/vectorize"
        cfg["output"]["prediction"] = f"{self.output_mount}/prediction.csv"
        cfg["output"]["log"] = f"{self.output_mount}/logs"

        if job_type == "train":
            cfg["output"]["model"] = f"{self.output_mount}/model"
        elif job_type in ("evaluate", "predict"):
            cfg["output"]["model"] = self.source_model_mount
        else:
            raise ValueError(f"unknown job_type: {job_type}")

        return cfg


def resolve_source_model_path(source_uri: str) -> str:
    """Given an MLflow artifact URI like 'runs:/<run_id>/model', return the
    artifact sub-path ('model'). Handles nested paths like 'runs:/<id>/model/sub'."""
    if not source_uri.startswith("runs:/"):
        raise ValueError(f"expected runs:/ URI, got {source_uri!r}")
    # runs:/<run_id>/<artifact_path>
    _, _, rest = source_uri.partition("runs:/")
    parts = rest.split("/", 1)
    if len(parts) < 2:
        return ""
    return parts[1]
```

- [ ] **Step 8: Run tests to confirm they pass**

```bash
cd backend && uv run pytest tests/test_services_job_config.py tests/test_services_job_tokens.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/job_config.py backend/app/services/job_tokens.py backend/tests/
git commit -m "feat(backend): add job token + config rendering services"
```

---

## Task 7: K8s Job Spec Generator

**Files:**
- Create: `backend/app/services/job_spec.py`
- Create: `backend/tests/test_services_job_spec.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Add job-pod settings**

Edit `backend/app/config.py`, add:

```python
    JOB_NAMESPACE: str = "lolday"
    JOB_HELPER_IMAGE: str = "harbor.harbor.svc:80/lolday/job-helper:v1"
    JOB_ACTIVE_DEADLINE_TRAIN_SECONDS: int = 21600      # 6h
    JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS: int = 1800    # 30m
    JOB_ACTIVE_DEADLINE_PREDICT_SECONDS: int = 3600     # 1h
    JOB_TTL_SECONDS_AFTER_FINISHED: int = 604800        # 7d
    JOB_NODE_SELECTOR_HOSTNAME: str = "server30"
    JOB_PER_USER_CONCURRENCY: int = 2
    JOB_IDEMPOTENCY_WINDOW_SECONDS: int = 300
    JOB_BACKEND_URL: str = "http://backend.lolday.svc:8000"
```

- [ ] **Step 2: Write failing tests**

Create `backend/tests/test_services_job_spec.py`:

```python
import uuid

import pytest

from app.models.job import JobType
from app.services.job_spec import (
    build_job_manifest,
    build_job_token_secret,
    job_name,
)


def test_job_name_deterministic_and_short():
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) == "job-train-00000000"
    # K8s job name must be ≤ 63 chars
    assert len(job_name(JobType.TRAIN, jid)) <= 63


def test_job_name_differs_per_type():
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) != job_name(JobType.EVALUATE, jid)
    assert job_name(JobType.EVALUATE, jid) != job_name(JobType.PREDICT, jid)


def test_build_job_token_secret_has_hashed_token():
    jid = uuid.uuid4()
    raw_token = "raw-abc123"
    secret = build_job_token_secret(jid, raw_token)
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == f"job-token-{jid.hex[:16]}"
    import base64
    decoded = base64.b64decode(secret["data"]["token"]).decode()
    assert decoded == raw_token


@pytest.fixture
def manifest_args():
    return dict(
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        job_type=JobType.TRAIN,
        detector_image="harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0",
        detector_cli_command="upxelfdet",
        mlflow_experiment_id="42",
        mlflow_run_id="abc123",
        mlflow_tracking_uri="http://mlflow.lolday.svc:5000",
        source_run_id=None,
        source_artifact_path=None,
        settings_override=None,
    )


def test_train_manifest_has_gpu_request_and_correct_args(manifest_args):
    m = build_job_manifest(**manifest_args)
    assert m["kind"] == "Job"
    assert m["spec"]["activeDeadlineSeconds"] == 21600
    assert m["spec"]["backoffLimit"] == 0
    assert m["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    main = next(
        c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
    )
    assert main["image"] == "harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0"
    assert main["command"] == ["upxelfdet"]
    assert main["args"] == ["train", "--config", "/mnt/config/config.json"]
    assert main["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert main["securityContext"]["readOnlyRootFilesystem"] is True
    env_keys = {e["name"] for e in main["env"]}
    assert "MLFLOW_TRACKING_URI" in env_keys
    assert "MLFLOW_RUN_ID" in env_keys


def test_eval_manifest_has_model_fetcher_init(manifest_args):
    args = {**manifest_args, "job_type": JobType.EVALUATE,
            "source_run_id": "xyz789", "source_artifact_path": "model"}
    m = build_job_manifest(**args)
    inits = m["spec"]["template"]["spec"]["initContainers"]
    names = [c["name"] for c in inits]
    assert "config-writer" in names
    assert "model-fetcher" in names
    fetcher = next(c for c in inits if c["name"] == "model-fetcher")
    env_keys = {e["name"] for e in fetcher["env"]}
    assert "SOURCE_RUN_ID" in env_keys


def test_train_manifest_has_no_model_fetcher(manifest_args):
    m = build_job_manifest(**manifest_args)
    inits = m["spec"]["template"]["spec"]["initContainers"]
    names = [c["name"] for c in inits]
    assert "model-fetcher" not in names


def test_predict_manifest_args(manifest_args):
    args = {**manifest_args, "job_type": JobType.PREDICT,
            "source_run_id": "abc", "source_artifact_path": "model"}
    m = build_job_manifest(**args)
    main = next(
        c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
    )
    assert main["args"] == ["predict", "--config", "/mnt/config/config.json"]
    assert m["spec"]["activeDeadlineSeconds"] == 3600


def test_manifest_has_samples_mounts(manifest_args):
    m = build_job_manifest(**manifest_args)
    mounts = {
        vm["name"]: vm for vm in next(
            c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
        )["volumeMounts"]
    }
    assert mounts["malware-samples"]["readOnly"] is True
    assert mounts["malware-samples"]["mountPath"] == "/mnt/samples/malware"
    assert mounts["benign-samples"]["readOnly"] is True


def test_manifest_labels_include_job_id(manifest_args):
    m = build_job_manifest(**manifest_args)
    pod_labels = m["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == "lolday-job"
    assert pod_labels["lolday.job-id"] == str(manifest_args["job_id"])
    assert pod_labels["lolday.job-type"] == "train"
```

- [ ] **Step 3: Confirm failing**

```bash
cd backend && uv run pytest tests/test_services_job_spec.py -v 2>&1 | tail -10
```

Expected: ModuleNotFoundError.

- [ ] **Step 4: Implement job_spec.py**

Create `backend/app/services/job_spec.py`:

```python
"""K8s Job manifest generator for detector train/eval/predict jobs.

Contract:
- Detector image's Dockerfile sets ENTRYPOINT to the per-detector CLI
  (e.g., `upxelfdet`). We override `command` with just the CLI binary so we
  can pass the action as `args` (this neutralizes the image's original
  ENTRYPOINT+CMD interplay and gives us explicit control).
- Standard mount paths match JobConfigRenderer in job_config.py.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from app.config import settings
from app.models.job import JobType

POD_LABEL_NAME = "lolday-job"


def job_name(job_type: JobType, job_id: uuid.UUID) -> str:
    """K8s Job name: `job-{type}-{id[:8]}`.

    Kubernetes object names must be ≤ 63 chars DNS-1123.
    """
    return f"job-{job_type.value}-{job_id.hex[:8]}"


def _active_deadline(job_type: JobType) -> int:
    return {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }[job_type]


def _job_token_secret_name(job_id: uuid.UUID) -> str:
    return f"job-token-{job_id.hex[:16]}"


def build_job_token_secret(job_id: uuid.UUID, raw_token: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": _job_token_secret_name(job_id),
            "namespace": settings.JOB_NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": POD_LABEL_NAME,
                "lolday.job-id": str(job_id),
            },
        },
        "type": "Opaque",
        "data": {
            "token": base64.b64encode(raw_token.encode("utf-8")).decode("ascii"),
        },
    }


def _config_writer_init(job_id: uuid.UUID) -> dict[str, Any]:
    return {
        "name": "config-writer",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.write_config"],
        "env": [
            {"name": "JOB_ID", "value": str(job_id)},
            {"name": "BACKEND_URL", "value": settings.JOB_BACKEND_URL},
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
            {"name": "config", "mountPath": "/mnt/config"},
        ],
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _model_fetcher_init(
    mlflow_tracking_uri: str,
    source_run_id: str,
    source_artifact_path: str,
) -> dict[str, Any]:
    return {
        "name": "model-fetcher",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.fetch_model"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "SOURCE_RUN_ID", "value": source_run_id},
            {"name": "ARTIFACT_PATH", "value": source_artifact_path},
            {"name": "TARGET_DIR", "value": "/mnt/source-model"},
        ],
        "volumeMounts": [
            {"name": "source-model", "mountPath": "/mnt/source-model"},
        ],
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _detector_container(
    detector_image: str,
    detector_cli_command: str,
    action: str,
    mlflow_tracking_uri: str,
    mlflow_run_id: str,
    mlflow_experiment_id: str,
    model_name: str,
) -> dict[str, Any]:
    return {
        "name": "detector",
        "image": detector_image,
        "imagePullPolicy": "IfNotPresent",
        "command": [detector_cli_command],
        "args": [action, "--config", "/mnt/config/config.json"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "MLFLOW_RUN_ID", "value": mlflow_run_id},
            {"name": "MLFLOW_EXPERIMENT_ID", "value": mlflow_experiment_id},
            {"name": "MLFLOW_MODEL_NAME", "value": model_name},
            {"name": "TMPDIR", "value": "/tmp"},
            {"name": "HOME", "value": "/tmp"},
        ],
        "volumeMounts": [
            {"name": "config", "mountPath": "/mnt/config", "readOnly": True},
            {"name": "output", "mountPath": "/mnt/output"},
            {"name": "source-model", "mountPath": "/mnt/source-model", "readOnly": True},
            {"name": "malware-samples", "mountPath": "/mnt/samples/malware", "readOnly": True},
            {"name": "benign-samples", "mountPath": "/mnt/samples/benign", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {
                "cpu": "4",
                "memory": "16Gi",
                "nvidia.com/gpu": 1,
            },
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def build_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    detector_image: str,
    detector_cli_command: str,
    mlflow_experiment_id: str,
    mlflow_run_id: str,
    mlflow_tracking_uri: str,
    source_run_id: str | None,
    source_artifact_path: str | None,
    settings_override: dict | None = None,
    model_name: str = "",
) -> dict[str, Any]:
    """Render a full K8s Job manifest as a Python dict (for `client.create_namespaced_job` via dict_to_obj or `BatchV1Api.create_namespaced_job(body=dict)`)."""

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
        {
            "name": "malware-samples",
            "persistentVolumeClaim": {"claimName": "malware-samples", "readOnly": True},
        },
        {
            "name": "benign-samples",
            "persistentVolumeClaim": {"claimName": "benign-samples", "readOnly": True},
        },
        {"name": "config", "emptyDir": {"sizeLimit": "32Mi"}},
        {"name": "output", "emptyDir": {"sizeLimit": "10Gi"}},
        {"name": "source-model", "emptyDir": {"sizeLimit": "2Gi"}},
        {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi", "medium": "Memory"}},
    ]

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": settings.JOB_NAMESPACE,
            "labels": pod_labels,
        },
        "spec": {
            "activeDeadlineSeconds": _active_deadline(job_type),
            "ttlSecondsAfterFinished": settings.JOB_TTL_SECONDS_AFTER_FINISHED,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "nodeSelector": {
                        "kubernetes.io/hostname": settings.JOB_NODE_SELECTOR_HOSTNAME
                    },
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
                            detector_cli_command=detector_cli_command,
                            action=job_type.value,
                            mlflow_tracking_uri=mlflow_tracking_uri,
                            mlflow_run_id=mlflow_run_id,
                            mlflow_experiment_id=mlflow_experiment_id,
                            model_name=model_name,
                        )
                    ],
                },
            },
        },
    }
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_services_job_spec.py -v 2>&1 | tail -25
```

Expected: 9 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/job_spec.py backend/app/config.py backend/tests/
git commit -m "feat(backend): add K8s Job manifest generator for detector runs"
```

---

## Task 8: Model Registry Service

**Files:**
- Create: `backend/app/services/model_registry.py`
- Create: `backend/tests/test_services_model_registry.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_services_model_registry.py`:

```python
import uuid

import pytest

from app.models.model_registry import ModelVersionStage
from app.services.model_registry import (
    InvalidTransitionError,
    validate_transition,
)


@pytest.mark.parametrize(
    "from_stage,to_stage",
    [
        (ModelVersionStage.NONE, ModelVersionStage.STAGING),
        (ModelVersionStage.STAGING, ModelVersionStage.PRODUCTION),
        (ModelVersionStage.PRODUCTION, ModelVersionStage.ARCHIVED),
        (ModelVersionStage.STAGING, ModelVersionStage.ARCHIVED),
        (ModelVersionStage.NONE, ModelVersionStage.PRODUCTION),     # skip Staging is allowed
    ],
)
def test_valid_forward_transitions(from_stage, to_stage):
    # actor role doesn't matter for forward transitions
    validate_transition(from_stage, to_stage, actor_role="developer", is_owner=True)


def test_archived_to_none_admin_only():
    with pytest.raises(InvalidTransitionError, match="admin"):
        validate_transition(
            ModelVersionStage.ARCHIVED,
            ModelVersionStage.NONE,
            actor_role="developer",
            is_owner=True,
        )
    validate_transition(
        ModelVersionStage.ARCHIVED,
        ModelVersionStage.NONE,
        actor_role="admin",
        is_owner=False,
    )


def test_archived_to_staging_admin_only():
    with pytest.raises(InvalidTransitionError, match="admin"):
        validate_transition(
            ModelVersionStage.ARCHIVED,
            ModelVersionStage.STAGING,
            actor_role="developer",
            is_owner=True,
        )


def test_user_role_denied_for_transitions():
    with pytest.raises(InvalidTransitionError, match="developer"):
        validate_transition(
            ModelVersionStage.STAGING,
            ModelVersionStage.PRODUCTION,
            actor_role="user",
            is_owner=True,
        )


def test_developer_must_be_owner():
    with pytest.raises(InvalidTransitionError, match="owner"):
        validate_transition(
            ModelVersionStage.NONE,
            ModelVersionStage.STAGING,
            actor_role="developer",
            is_owner=False,
        )


def test_admin_can_transition_anyone():
    validate_transition(
        ModelVersionStage.NONE,
        ModelVersionStage.PRODUCTION,
        actor_role="admin",
        is_owner=False,
    )


def test_same_stage_is_noop_no_error():
    # Transitioning to the same stage is allowed (idempotent)
    validate_transition(
        ModelVersionStage.PRODUCTION,
        ModelVersionStage.PRODUCTION,
        actor_role="admin",
        is_owner=False,
    )
```

- [ ] **Step 2: Confirm failing**

```bash
cd backend && uv run pytest tests/test_services_model_registry.py -v 2>&1 | tail -10
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement model_registry service**

Create `backend/app/services/model_registry.py`:

```python
"""Model Registry transition rules and MLflow sync.

Transition matrix:

  source \\ target  | None     | Staging  | Production | Archived
  ------------------|----------|----------|------------|----------
  None              | noop     | D/O or A | D/O or A   | D/O or A
  Staging           | admin    | noop     | D/O or A   | D/O or A
  Production        | admin    | admin    | noop       | D/O or A
  Archived          | admin    | admin    | admin      | noop

Legend: D/O = developer (must be owner); A = admin; admin = admin only.
"""

from __future__ import annotations

from app.models.model_registry import ModelVersionStage


class InvalidTransitionError(ValueError):
    pass


_ADMIN_ONLY_TARGETS_FROM_ARCHIVED = {
    ModelVersionStage.NONE,
    ModelVersionStage.STAGING,
    ModelVersionStage.PRODUCTION,
}

_ADMIN_ONLY_TARGETS_FROM_PRODUCTION = {
    ModelVersionStage.NONE,
    ModelVersionStage.STAGING,
}

_ADMIN_ONLY_TARGETS_FROM_STAGING = {
    ModelVersionStage.NONE,
}


def validate_transition(
    from_stage: ModelVersionStage,
    to_stage: ModelVersionStage,
    *,
    actor_role: str,
    is_owner: bool,
) -> None:
    """Raise InvalidTransitionError if not allowed.

    `actor_role` is one of 'admin', 'developer', 'user'.
    `is_owner` is True iff actor owns the source job that produced this model.
    """
    if from_stage == to_stage:
        return  # noop

    if actor_role == "admin":
        return  # admin unrestricted

    if actor_role != "developer":
        raise InvalidTransitionError(
            f"role {actor_role!r}: only developer or admin can transition model stages"
        )

    if not is_owner:
        raise InvalidTransitionError(
            "non-owner developer cannot transition; must be model owner or admin"
        )

    # from_stage-based admin-only targets
    admin_only = set()
    if from_stage == ModelVersionStage.ARCHIVED:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_ARCHIVED
    elif from_stage == ModelVersionStage.PRODUCTION:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_PRODUCTION
    elif from_stage == ModelVersionStage.STAGING:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_STAGING

    if to_stage in admin_only:
        raise InvalidTransitionError(
            f"transition {from_stage.value} → {to_stage.value} requires admin"
        )
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_services_model_registry.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/model_registry.py backend/tests/
git commit -m "feat(backend): add model registry transition rule validation"
```

---

## Task 9: Job Schemas + Router + Internal Config Endpoint

**Files:**
- Modify: `backend/app/schemas/job.py`
- Modify: `backend/app/schemas/__init__.py`
- Create: `backend/app/routers/jobs.py`
- Modify: `backend/app/routers/internal.py`
- Modify: `backend/app/deps.py` (+ require_job_token)
- Create: `backend/tests/test_jobs.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write Pydantic schemas**

Replace `backend/app/schemas/job.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.job import JobStatus, JobType, ResourceProfile


class JobCreate(BaseModel):
    type: JobType
    detector_version_id: uuid.UUID
    train_dataset_id: uuid.UUID | None = None
    test_dataset_id: uuid.UUID | None = None
    predict_dataset_id: uuid.UUID | None = None
    source_model_version_id: uuid.UUID | None = None
    params: dict[str, Any] = {}
    resource_profile: ResourceProfile = ResourceProfile.STANDARD

    @model_validator(mode="after")
    def _validate_refs_per_type(self) -> "JobCreate":
        if self.type == JobType.TRAIN:
            if self.train_dataset_id is None:
                raise ValueError("train_dataset_id required for type=train")
            if self.source_model_version_id is not None:
                raise ValueError("source_model_version_id must be null for type=train")
            if self.predict_dataset_id is not None:
                raise ValueError("predict_dataset_id must be null for type=train")
        elif self.type == JobType.EVALUATE:
            if self.test_dataset_id is None:
                raise ValueError("test_dataset_id required for type=evaluate")
            if self.source_model_version_id is None:
                raise ValueError("source_model_version_id required for type=evaluate")
            if self.train_dataset_id is not None or self.predict_dataset_id is not None:
                raise ValueError("only test_dataset_id allowed for type=evaluate")
        elif self.type == JobType.PREDICT:
            if self.predict_dataset_id is None:
                raise ValueError("predict_dataset_id required for type=predict")
            if self.source_model_version_id is None:
                raise ValueError("source_model_version_id required for type=predict")
            if self.train_dataset_id is not None or self.test_dataset_id is not None:
                raise ValueError("only predict_dataset_id allowed for type=predict")
        return self


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    detector_version_id: uuid.UUID
    owner_id: uuid.UUID
    mlflow_run_id: str | None
    k8s_job_name: str | None
    failure_reason: str | None
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobRead(JobSummary):
    train_dataset_id: uuid.UUID | None
    test_dataset_id: uuid.UUID | None
    predict_dataset_id: uuid.UUID | None
    source_model_version_id: uuid.UUID | None
    resolved_config: dict
    log_tail: str | None
    summary_metrics: dict | None
    resource_profile: ResourceProfile
    mlflow_experiment_id: str | None


class JobList(BaseModel):
    items: list[JobSummary]
    total: int
    page: int
    page_size: int


class JobInternalConfig(BaseModel):
    """Payload returned by `/internal/jobs/{id}/config` for the config-writer init container."""
    config: dict
    train_csv: str | None
    test_csv: str | None
    predict_csv: str | None
```

- [ ] **Step 2: Uncomment schema exports**

Edit `backend/app/schemas/__init__.py`, uncomment job lines:

```python
from app.schemas.job import JobCreate, JobRead, JobSummary
```

And add to `__all__`.

- [ ] **Step 3: Add require_job_token dep**

Edit `backend/app/deps.py`, append:

```python
from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.db import get_async_session
from app.models import Job
from app.services.job_tokens import verify_token


async def require_job_token(
    job_id: uuid.UUID,
    authorization: Annotated[str, Header()],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> Job:
    """Authenticate as a given job's init container via one-time token.

    Expected header: `Authorization: Bearer <token>`
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    job = await session.get(Job, job_id)
    if job is None or job.token_hash is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not verify_token(token, job.token_hash):
        raise HTTPException(status_code=403, detail="invalid token")
    return job
```

(Make sure `uuid` and `Depends` are imported at the top of deps.py.)

- [ ] **Step 4: Write failing tests**

Create `backend/tests/test_jobs.py`:

```python
import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_train_job_happy_path(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"seed": 42},
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] in ("pending", "preparing")
    assert body["type"] == "train"
    assert body["mlflow_run_id"]


@pytest.mark.asyncio
async def test_create_job_type_mismatch_rejected(user_client, seed_detector_version):
    dv_id = await seed_detector_version()
    # train without train_dataset_id
    r = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id, "params": {}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_evaluate_requires_source_model(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    test_ds = await seed_dataset(name="te-ds")
    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "evaluate",
            "detector_version_id": dv_id,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_duplicate_submission(
    user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    payload = {
        "type": "train",
        "detector_version_id": dv_id,
        "train_dataset_id": tr,
        "test_dataset_id": te,
        "params": {"seed": 1},
    }
    r1 = await user_client.post("/api/v1/jobs", json=payload)
    assert r1.status_code == 202
    r2 = await user_client.post("/api/v1/jobs", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_concurrency_limit_enforced(
    user_client, seed_detector_version, seed_dataset, monkeypatch
):
    from app.config import settings
    monkeypatch.setattr(settings, "JOB_PER_USER_CONCURRENCY", 1)

    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")

    r1 = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 1}},
    )
    assert r1.status_code == 202

    r2 = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 2}},
    )
    assert r2.status_code == 429


@pytest.mark.asyncio
async def test_list_jobs_owner_scoped(user_client, second_user_client,
                                       seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 1}},
    )
    r = await second_user_client.get("/api/v1/jobs")
    # second user sees 0 items (jobs are owner-scoped, public only via explicit filter)
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_cancel_job(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    cr = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {}},
    )
    jid = cr.json()["id"]
    r = await user_client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_internal_config_endpoint_requires_token(
    user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    cr = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {}},
    )
    jid = cr.json()["id"]

    # Without token
    r = await user_client.get(f"/api/v1/internal/jobs/{jid}/config")
    assert r.status_code == 401
```

Fixtures `seed_detector_version`, `seed_dataset`, `second_user_client` need to be added in `tests/conftest.py`. Pattern:

```python
@pytest.fixture
async def seed_detector_version(db_session, seed_user):
    """Return a callable that inserts a minimal DetectorVersion row."""
    async def _seed(name: str = "upxelfdet", git_tag: str = "v0.4.0"):
        from app.models import Detector, DetectorVersion
        from app.models.detector import DetectorVersionStatus
        det = Detector(name=name, display_name=name, git_url=f"https://github.com/test/{name}.git", owner_id=seed_user.id)
        db_session.add(det)
        await db_session.flush()
        dv = DetectorVersion(
            detector_id=det.id,
            git_tag=git_tag,
            git_sha="a" * 40,
            harbor_image=f"harbor.harbor.svc:80/detectors/{name}:{git_tag}",
            image_digest="sha256:" + "a" * 64,
            config_schema={"type": "object", "properties": {"seed": {"type": "integer"}}},
            status=DetectorVersionStatus.ACTIVE,
        )
        db_session.add(dv)
        await db_session.commit()
        return str(dv.id)
    return _seed


@pytest.fixture
async def seed_dataset(user_client):
    from pathlib import Path
    FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()
    async def _seed(name: str = "ds"):
        r = await user_client.post(
            "/api/v1/datasets",
            json={"name": name, "csv_content": FIXTURE_CSV},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]
    return _seed
```

Also `second_user_client` as in Task 5 conftest addition.

- [ ] **Step 5: Mock K8s + MLflow at conftest level**

For job tests we need K8s Job creation + MLflow experiment/run creation to not actually call APIs. Extend conftest:

```python
@pytest.fixture(autouse=True)
def mock_k8s_batch(monkeypatch):
    """Autouse: replace kubernetes BatchV1Api create/delete with in-memory stubs."""
    class _StubBatch:
        def __init__(self):
            self.jobs = {}
        def create_namespaced_job(self, namespace, body, **kw):
            name = body["metadata"]["name"] if isinstance(body, dict) else body.metadata.name
            self.jobs[name] = body
            return body
        def delete_namespaced_job(self, name, namespace, **kw):
            self.jobs.pop(name, None)
        def read_namespaced_job(self, name, namespace, **kw):
            from kubernetes.client.exceptions import ApiException
            if name not in self.jobs:
                raise ApiException(status=404)
            class _S: status = type("S", (), {"succeeded": None, "failed": None})()
            return _S()
    stub = _StubBatch()
    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: stub)

    class _StubCore:
        def create_namespaced_secret(self, namespace, body, **kw): return body
        def delete_namespaced_secret(self, name, namespace, **kw): pass
        def list_namespaced_pod(self, namespace, **kw):
            class _R: items = []
            return _R()
    monkeypatch.setattr("app.services.k8s.core_v1", lambda: _StubCore())


@pytest.fixture(autouse=True)
def mock_mlflow(monkeypatch):
    class _Stub:
        exp_counter = 0
        run_counter = 0

        async def get_or_create_experiment(self, name, artifact_location=None):
            _Stub.exp_counter += 1
            return f"exp-{_Stub.exp_counter}"

        async def create_run(self, experiment_id, tags=None):
            _Stub.run_counter += 1
            return f"run-{_Stub.run_counter}"

        async def get_run(self, run_id):
            return {"info": {"status": "FINISHED", "run_id": run_id, "experiment_id": "exp-1"},
                    "data": {"metrics": {"accuracy": 0.9}, "tags": {}, "params": {}}}

        async def update_run(self, run_id, **kw): pass
        async def set_run_tag(self, *a, **kw): pass

    stub = _Stub()
    import app.services.mlflow_client as mc
    original = mc.MlflowClient
    monkeypatch.setattr(mc, "MlflowClient", lambda *a, **kw: stub)
```

These stubs are sufficient for API tests; real MLflow/K8s interaction is covered by integration + E2E.

- [ ] **Step 6: Confirm failing**

```bash
cd backend && uv run pytest tests/test_jobs.py -v 2>&1 | tail -15
```

Expected: ModuleNotFoundError or 404.

- [ ] **Step 7: Implement job router**

Create `backend/app/routers/jobs.py`:

```python
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.deps import current_active_user
from app.models import DatasetConfig, DetectorVersion, Job, ModelVersion, User
from app.models.dataset import DatasetVisibility
from app.models.job import JobStatus, JobType, NON_TERMINAL_STATUSES
from app.schemas.job import JobCreate, JobList, JobRead, JobSummary
from app.services.dataset import DatasetIntegrityError, spot_check_samples
from app.services.job_config import (
    JobConfigRenderer,
    compute_idempotency_key,
    resolve_source_model_path,
)
from app.services.job_spec import build_job_manifest, build_job_token_secret, job_name
from app.services.job_tokens import generate_token, hash_token
from app.services.k8s import batch_v1, core_v1
from app.services.mlflow_client import MlflowClient

router = APIRouter()


def _get_mlflow_client() -> MlflowClient:
    return MlflowClient(settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)


async def _load_dataset(
    ds_id: uuid.UUID, session: AsyncSession, user: User, field: str
) -> DatasetConfig:
    if ds_id is None:
        return None
    ds = await session.get(DatasetConfig, ds_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(status_code=422, detail=f"{field}: dataset not found or deleted")
    if (
        ds.visibility == DatasetVisibility.PRIVATE
        and ds.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise HTTPException(status_code=422, detail=f"{field}: dataset not accessible")
    return ds


@router.post("", status_code=202, response_model=JobRead)
async def create_job(
    body: JobCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    # 1. Load detector_version
    dv = await session.get(DetectorVersion, body.detector_version_id)
    if dv is None:
        raise HTTPException(status_code=422, detail="detector_version not found")

    # 2. Load dataset refs
    train_ds = await _load_dataset(body.train_dataset_id, session, user, "train_dataset_id")
    test_ds = await _load_dataset(body.test_dataset_id, session, user, "test_dataset_id")
    predict_ds = await _load_dataset(body.predict_dataset_id, session, user, "predict_dataset_id")

    # 3. Load source model (eval/predict)
    source_run_id = None
    source_model = None
    if body.source_model_version_id is not None:
        source_model = await session.get(ModelVersion, body.source_model_version_id)
        if source_model is None:
            raise HTTPException(status_code=422, detail="source_model_version not found")
        source_run_id = source_model.mlflow_run_id

    # 4. Validate params against schema
    try:
        jsonschema.validate(instance=body.params, schema=dv.config_schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(status_code=422, detail=f"params invalid: {e.message}")

    # 5. Idempotency key
    idem_key = compute_idempotency_key(
        user_id=str(user.id),
        detector_version_id=str(dv.id),
        job_type=body.type.value,
        train_ds=str(train_ds.id) if train_ds else None,
        test_ds=str(test_ds.id) if test_ds else None,
        predict_ds=str(predict_ds.id) if predict_ds else None,
        source_model=str(source_model.id) if source_model else None,
        params=body.params,
    )
    window_start = datetime.now(timezone.utc) - timedelta(
        seconds=settings.JOB_IDEMPOTENCY_WINDOW_SECONDS
    )
    dup = (await session.execute(
        select(Job).where(
            Job.idempotency_key == idem_key,
            Job.submitted_at >= window_start,
            Job.status.in_(NON_TERMINAL_STATUSES),
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"duplicate submission; existing job: {dup.id}")

    # 6. Concurrency cap
    in_flight = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.owner_id == user.id,
            Job.status.in_(NON_TERMINAL_STATUSES),
        )
    )).scalar_one()
    if in_flight >= settings.JOB_PER_USER_CONCURRENCY:
        raise HTTPException(status_code=429, detail=f"in-flight limit ({settings.JOB_PER_USER_CONCURRENCY}) reached")

    # 7. Dataset integrity spot-check (only on real file system; skip if samples dir absent in test env)
    from pathlib import Path
    samples_root = Path(settings.SAMPLES_LOCAL_ROOT)
    if samples_root.exists():
        try:
            for ds in (train_ds, test_ds, predict_ds):
                if ds is None:
                    continue
                parsed = _reparse_csv(ds)
                spot_check_samples(
                    file_names=parsed.file_names,
                    labels=parsed.labels,
                    samples_root=samples_root,
                    sample_count=settings.DATASET_SPOT_CHECK_COUNT,
                    missing_threshold=settings.DATASET_SPOT_CHECK_MISSING_THRESHOLD,
                )
        except DatasetIntegrityError as e:
            raise HTTPException(status_code=422, detail=f"dataset_integrity_failed: {e}")

    # 8. MLflow experiment + run
    client = _get_mlflow_client()
    exp_name = f"detector:{dv.detector_id}:{dv.git_tag}"
    if not dv.mlflow_experiment_id:
        dv.mlflow_experiment_id = await client.get_or_create_experiment(exp_name)
        await session.commit()
    run_id = await client.create_run(dv.mlflow_experiment_id)
    await client.set_run_tag(run_id, "maldet.action", body.type.value)
    await client.set_run_tag(run_id, "lolday.user", str(user.id))
    await client.set_run_tag(run_id, "lolday.detector_version", str(dv.id))

    # 9. Render resolved_config (fetch detector defaults from its config_schema's default fields)
    detector_defaults = _extract_defaults(dv.config_schema)
    renderer = JobConfigRenderer(
        samples_root=settings.SAMPLES_ROOT,
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    resolved = renderer.render(
        job_type=body.type.value,
        detector_defaults=detector_defaults,
        user_params=body.params,
    )

    # 10. Create job row
    raw_token = generate_token()
    job = Job(
        type=body.type,
        status=JobStatus.PENDING,
        detector_version_id=dv.id,
        train_dataset_id=train_ds.id if train_ds else None,
        test_dataset_id=test_ds.id if test_ds else None,
        predict_dataset_id=predict_ds.id if predict_ds else None,
        source_model_version_id=source_model.id if source_model else None,
        owner_id=user.id,
        resolved_config=resolved,
        mlflow_experiment_id=dv.mlflow_experiment_id,
        mlflow_run_id=run_id,
        idempotency_key=idem_key,
        token_hash=hash_token(raw_token),
        resource_profile=body.resource_profile,
    )
    session.add(job)
    await session.flush()

    # 11. Launch K8s Job
    secret = build_job_token_secret(job.id, raw_token)
    core_v1().create_namespaced_secret(namespace=settings.JOB_NAMESPACE, body=secret)
    manifest = build_job_manifest(
        job_id=job.id,
        job_type=body.type,
        detector_image=dv.harbor_image,
        detector_cli_command=_detector_cli(dv),
        mlflow_experiment_id=dv.mlflow_experiment_id,
        mlflow_run_id=run_id,
        mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
        source_run_id=source_run_id,
        source_artifact_path=(
            resolve_source_model_path(f"runs:/{source_run_id}/model")
            if source_run_id else None
        ),
        model_name=_registered_model_name(dv),
    )
    try:
        batch_v1().create_namespaced_job(namespace=settings.JOB_NAMESPACE, body=manifest)
    except Exception:
        # Clean up token Secret on Job creation failure
        try:
            core_v1().delete_namespaced_secret(
                name=secret["metadata"]["name"], namespace=settings.JOB_NAMESPACE
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to create K8s Job")

    job.k8s_job_name = manifest["metadata"]["name"]
    job.status = JobStatus.PREPARING
    await session.commit()
    await session.refresh(job)
    return JobRead.model_validate(job)


@router.get("", response_model=JobList)
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    type: JobType | None = None,
    status_: JobStatus | None = Query(None, alias="status"),
    detector_id: uuid.UUID | None = None,
) -> JobList:
    filters = []
    # Users see their own jobs; admins see all
    if user.role.value != "admin":
        filters.append(Job.owner_id == user.id)
    if type is not None:
        filters.append(Job.type == type)
    if status_ is not None:
        filters.append(Job.status == status_)
    if detector_id is not None:
        # Join via detector_version
        filters.append(
            Job.detector_version_id.in_(
                select(DetectorVersion.id).where(DetectorVersion.detector_id == detector_id)
            )
        )

    count_stmt = select(func.count()).select_from(Job)
    if filters:
        count_stmt = count_stmt.where(and_(*filters))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = select(Job).order_by(Job.submitted_at.desc()).offset((page - 1) * page_size).limit(page_size)
    if filters:
        stmt = stmt.where(and_(*filters))
    items = (await session.execute(stmt)).scalars().all()

    return JobList(
        items=[JobSummary.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="job not found")
    return JobRead.model_validate(job)


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in NON_TERMINAL_STATUSES or job.finished_at is None:
        # Live: proxy K8s API
        return _stream_live_logs(job)
    # Within 24h post-finalize: use log_tail
    age = datetime.now(timezone.utc) - job.finished_at.replace(tzinfo=timezone.utc)
    if age.total_seconds() > 86400:
        return Response(
            content=job.log_tail or "",
            status_code=410,
            media_type="text/plain",
        )
    return Response(content=job.log_tail or "", media_type="text/plain")


def _stream_live_logs(job: Job):
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={job.id}",
        )
        if not pods.items:
            return Response(content="", media_type="text/plain")
        pod = pods.items[0]
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.JOB_NAMESPACE,
            container="detector",
            tail_lines=1000,
        )
        return Response(content=log, media_type="text/plain")
    except Exception:
        return Response(content="(logs unavailable)", media_type="text/plain", status_code=503)


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"job already {job.status.value}")

    if job.k8s_job_name:
        try:
            batch_v1().delete_namespaced_job(
                name=job.k8s_job_name,
                namespace=settings.JOB_NAMESPACE,
                propagation_policy="Background",
            )
        except Exception:
            pass

    job.status = JobStatus.CANCELLED
    job.failure_reason = "cancelled_by_user" if job.owner_id == user.id else "cancelled_by_admin"
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)
    return JobRead.model_validate(job)


# ------------- helpers -------------


def _extract_defaults(schema: dict) -> dict:
    """Pull default values out of a Pydantic-generated JSON schema to reconstruct
    the detector's default config.

    Pydantic stores defaults per property in `properties.{name}.default`.
    For nested objects, recurse via `$defs` references.
    """
    defaults: dict = {}
    properties = schema.get("properties", {})
    defs = schema.get("$defs", {})

    for key, prop in properties.items():
        if "default" in prop:
            defaults[key] = prop["default"]
        elif "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            ref_schema = defs.get(ref_name, {})
            nested = _extract_defaults(ref_schema)
            if nested:
                defaults[key] = nested
    return defaults


def _detector_cli(dv: DetectorVersion) -> str:
    """Derive the CLI entrypoint from the detector's name.

    Follows convention: detector's pyproject.toml [project.scripts] uses the
    detector's slugified package name as the command. Phase 3 stored this
    as detector.name.
    """
    from app.models import Detector
    det = dv.detector  # lazy-loaded; caller should have relationship loaded
    if det is not None:
        return det.name
    return str(dv.detector_id)  # fallback


def _registered_model_name(dv: DetectorVersion) -> str:
    """Model Registry model name = detector's slug."""
    from app.models import Detector
    det = dv.detector
    if det is not None:
        return det.name
    return str(dv.detector_id)


def _reparse_csv(ds: DatasetConfig):
    """Re-run parse_csv to get file_names list (we don't store it in DB to save space)."""
    from app.services.dataset import parse_csv
    return parse_csv(ds.csv_content)
```

Note on `dv.detector`: the `Detector.versions` relationship is defined in Phase 3's detector model. Ensure the backref is configured so `dv.detector` works. If not, adjust in a one-line edit to `detector.py`:

```python
# In Detector model
versions: Mapped[list["DetectorVersion"]] = relationship(back_populates="detector", lazy="selectin")
```

And in DetectorVersion:

```python
detector: Mapped["Detector"] = relationship(back_populates="versions", lazy="selectin")
```

- [ ] **Step 8: Implement internal config endpoint**

Edit `backend/app/routers/internal.py`, add:

```python
from app.deps import require_job_token
from app.models import Job, DatasetConfig
from app.schemas.job import JobInternalConfig


@router.get("/jobs/{job_id}/config", response_model=JobInternalConfig)
async def internal_get_job_config(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    job: Annotated[Job, Depends(require_job_token)],
) -> JobInternalConfig:
    train_csv = None
    test_csv = None
    predict_csv = None
    if job.train_dataset_id:
        ds = await session.get(DatasetConfig, job.train_dataset_id)
        train_csv = ds.csv_content if ds else None
    if job.test_dataset_id:
        ds = await session.get(DatasetConfig, job.test_dataset_id)
        test_csv = ds.csv_content if ds else None
    if job.predict_dataset_id:
        ds = await session.get(DatasetConfig, job.predict_dataset_id)
        predict_csv = ds.csv_content if ds else None
    return JobInternalConfig(
        config=job.resolved_config,
        train_csv=train_csv,
        test_csv=test_csv,
        predict_csv=predict_csv,
    )
```

- [ ] **Step 9: Register router in main.py**

Edit `backend/app/main.py`:

```python
from app.routers import admin, credentials, datasets, detectors, internal, jobs
```

And:

```python
app.include_router(
    jobs.router,
    prefix="/api/v1/jobs",
    tags=["jobs"],
)
```

- [ ] **Step 10: Run tests**

```bash
cd backend && uv run pytest tests/test_jobs.py -v 2>&1 | tail -25
```

Expected: all pass.

- [ ] **Step 11: Un-xfail the blocked-delete test**

Edit `backend/tests/test_datasets.py`, change:

```python
@pytest.mark.asyncio
async def test_delete_dataset_blocked_by_active_job(
    user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    r = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {}},
    )
    assert r.status_code == 202

    r = await user_client.delete(f"/api/v1/datasets/{tr}")
    assert r.status_code == 409
```

Remove the `xfail`.

- [ ] **Step 12: Run full test suite to confirm green**

```bash
cd backend && uv run pytest -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 13: Commit**

```bash
git add backend/app/routers/ backend/app/schemas/ backend/app/deps.py backend/app/main.py backend/tests/
git commit -m "feat(backend): add job CRUD + internal config endpoint"
```

---

## Task 10: Model Registry Router + MLflow Proxy Router

**Files:**
- Modify: `backend/app/schemas/model_registry.py`
- Modify: `backend/app/schemas/__init__.py`
- Create: `backend/app/routers/models_registry.py`
- Create: `backend/app/routers/experiments_proxy.py`
- Create: `backend/tests/test_models_registry.py`
- Create: `backend/tests/test_experiments_proxy.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write model-registry schemas**

Replace `backend/app/schemas/model_registry.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.model_registry import ModelVersionStage


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mlflow_name: str
    mlflow_version: int
    mlflow_run_id: str
    current_stage: ModelVersionStage
    detector_version_id: uuid.UUID
    source_job_id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime
    last_transitioned_at: datetime


class ModelVersionList(BaseModel):
    items: list[ModelVersionRead]
    total: int
    page: int
    page_size: int


class RegisteredModelSummary(BaseModel):
    name: str
    latest_version: int | None
    latest_production_version: int | None
    latest_staging_version: int | None


class ModelTransitionRequest(BaseModel):
    to_stage: ModelVersionStage
    comment: str | None = None
```

- [ ] **Step 2: Uncomment schema exports**

Edit `backend/app/schemas/__init__.py`:

```python
from app.schemas.model_registry import (
    ModelTransitionRequest,
    ModelVersionRead,
)
```

And add to `__all__`.

- [ ] **Step 3: Write failing router tests**

Create `backend/tests/test_models_registry.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_list_models_empty(user_client):
    r = await user_client.get("/api/v1/models")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_model_versions_requires_name(user_client, seed_model_version):
    name, version = await seed_model_version()
    r = await user_client.get(f"/api/v1/models/{name}/versions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["mlflow_version"] == version


@pytest.mark.asyncio
async def test_transition_to_production_auto_archives_existing(
    user_client, seed_model_version
):
    name, v1 = await seed_model_version()
    name2, v2 = await seed_model_version(name=name)  # second version of same model

    # Transition v1 to Production
    r = await user_client.post(
        f"/api/v1/models/{name}/versions/{v1}/transition",
        json={"to_stage": "Production", "comment": "first prod"},
    )
    assert r.status_code == 200

    # Transition v2 to Production → MLflow archives v1
    r2 = await user_client.post(
        f"/api/v1/models/{name}/versions/{v2}/transition",
        json={"to_stage": "Production", "comment": "newer prod"},
    )
    assert r2.status_code == 200
    # v1 should now be Archived (verify via GET)
    g = await user_client.get(f"/api/v1/models/{name}/versions/{v1}")
    assert g.json()["current_stage"] == "Archived"


@pytest.mark.asyncio
async def test_transition_denied_to_user_role(
    user_role_client, seed_model_version
):
    name, v = await seed_model_version()
    r = await user_role_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging"},
    )
    assert r.status_code in (403, 422)  # depending on impl detail


@pytest.mark.asyncio
async def test_transition_denied_to_non_owner_developer(
    user_client, second_user_client, seed_model_version
):
    name, v = await seed_model_version()  # created by `user_client`'s user
    r = await second_user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging"},
    )
    # Second user is also developer but doesn't own the model
    assert r.status_code in (403, 422)


@pytest.mark.asyncio
async def test_transition_writes_audit_log(user_client, seed_model_version, db_session):
    from app.models.model_registry import ModelTransitionLog
    from sqlalchemy import select

    name, v = await seed_model_version()
    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging", "comment": "test"},
    )
    logs = (await db_session.execute(select(ModelTransitionLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].to_stage.value == "Staging"
    assert logs[0].comment == "test"


@pytest.mark.asyncio
async def test_delete_model_version_only_none_or_archived(
    user_client, seed_model_version
):
    name, v = await seed_model_version()
    # Promote to Staging
    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging"},
    )
    # Try delete while in Staging → denied
    r = await user_client.delete(f"/api/v1/models/{name}/versions/{v}")
    assert r.status_code == 409

    # Archive it
    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Archived"},
    )
    # Now deletable
    r2 = await user_client.delete(f"/api/v1/models/{name}/versions/{v}")
    assert r2.status_code == 204
```

New fixture needed — add to `conftest.py`:

```python
@pytest.fixture
async def seed_model_version(db_session, seed_user, seed_detector_version, seed_dataset):
    """Insert a ModelVersion row tied to a fresh detector_version + fake source job."""
    from app.models import Job, ModelVersion, User
    from app.models.job import JobStatus, JobType
    from app.models.model_registry import ModelVersionStage
    from uuid import UUID, uuid4

    async def _seed(name: str = "upxelfdet"):
        dv_id_str = await seed_detector_version(name=name)
        ds_id_str = await seed_dataset(name=f"ds-for-{name}")
        # Create a fake succeeded training job
        job = Job(
            type=JobType.TRAIN,
            status=JobStatus.SUCCEEDED,
            detector_version_id=UUID(dv_id_str),
            train_dataset_id=UUID(ds_id_str),
            test_dataset_id=UUID(ds_id_str),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid4().hex[:8]}",
            idempotency_key=uuid4().hex,
        )
        db_session.add(job)
        await db_session.flush()

        # Determine next MLflow version number for this model name
        from sqlalchemy import select, func
        row = await db_session.execute(
            select(func.coalesce(func.max(ModelVersion.mlflow_version), 0)).where(
                ModelVersion.mlflow_name == name
            )
        )
        next_version = row.scalar_one() + 1

        mv = ModelVersion(
            mlflow_name=name,
            mlflow_version=next_version,
            mlflow_run_id=job.mlflow_run_id,
            current_stage=ModelVersionStage.NONE,
            detector_version_id=UUID(dv_id_str),
            source_job_id=job.id,
            owner_id=seed_user.id,
        )
        db_session.add(mv)
        await db_session.commit()
        await db_session.refresh(mv)
        return name, next_version
    return _seed
```

Also need `user_role_client` (simple user, not developer). Add:

```python
@pytest.fixture
async def user_role_client(client_factory):
    from app.models import Role
    async with client_factory(
        email="regular@example.com", password="pass1234", role=Role.USER
    ) as c:
        yield c
```

(`client_factory` should accept a `role` kwarg — add if missing.)

- [ ] **Step 4: Confirm failing**

```bash
cd backend && uv run pytest tests/test_models_registry.py -v 2>&1 | tail -15
```

Expected: 404 / ModuleNotFoundError.

- [ ] **Step 5: Implement model registry router**

Create `backend/app/routers/models_registry.py`:

```python
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.deps import current_active_user
from app.models import ModelTransitionLog, ModelVersion, User
from app.models.model_registry import ModelVersionStage
from app.schemas.model_registry import (
    ModelTransitionRequest,
    ModelVersionList,
    ModelVersionRead,
    RegisteredModelSummary,
)
from app.services.mlflow_client import MlflowClient
from app.services.model_registry import InvalidTransitionError, validate_transition

router = APIRouter()


def _mlflow() -> MlflowClient:
    return MlflowClient(settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)


@router.get("", response_model=list[RegisteredModelSummary])
async def list_registered_models(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> list[RegisteredModelSummary]:
    """Summary per registered model (grouped by mlflow_name)."""
    # Aggregate via SQL group-by
    stmt = (
        select(
            ModelVersion.mlflow_name,
            func.max(ModelVersion.mlflow_version).label("latest"),
        )
        .group_by(ModelVersion.mlflow_name)
    )
    names = (await session.execute(stmt)).all()

    summaries = []
    for name, latest in names:
        latest_prod = (await session.execute(
            select(func.max(ModelVersion.mlflow_version)).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
            )
        )).scalar_one()
        latest_staging = (await session.execute(
            select(func.max(ModelVersion.mlflow_version)).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.current_stage == ModelVersionStage.STAGING,
            )
        )).scalar_one()
        summaries.append(RegisteredModelSummary(
            name=name,
            latest_version=latest,
            latest_production_version=latest_prod,
            latest_staging_version=latest_staging,
        ))
    return summaries


@router.get("/{name}", response_model=RegisteredModelSummary)
async def get_registered_model(
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> RegisteredModelSummary:
    stmt = select(func.max(ModelVersion.mlflow_version)).where(
        ModelVersion.mlflow_name == name,
    )
    latest = (await session.execute(stmt)).scalar_one()
    if latest is None:
        raise HTTPException(status_code=404, detail="model not found")
    latest_prod = (await session.execute(
        select(func.max(ModelVersion.mlflow_version)).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
        )
    )).scalar_one()
    latest_staging = (await session.execute(
        select(func.max(ModelVersion.mlflow_version)).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.current_stage == ModelVersionStage.STAGING,
        )
    )).scalar_one()
    return RegisteredModelSummary(
        name=name,
        latest_version=latest,
        latest_production_version=latest_prod,
        latest_staging_version=latest_staging,
    )


@router.get("/{name}/versions", response_model=ModelVersionList)
async def list_model_versions(
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    stage: ModelVersionStage | None = None,
) -> ModelVersionList:
    filters = [ModelVersion.mlflow_name == name]
    if stage is not None:
        filters.append(ModelVersion.current_stage == stage)

    count = (await session.execute(
        select(func.count()).select_from(ModelVersion).where(*filters)
    )).scalar_one()
    items = (await session.execute(
        select(ModelVersion)
        .where(*filters)
        .order_by(ModelVersion.mlflow_version.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return ModelVersionList(
        items=[ModelVersionRead.model_validate(m) for m in items],
        total=count,
        page=page,
        page_size=page_size,
    )


@router.get("/{name}/versions/{version}", response_model=ModelVersionRead)
async def get_model_version(
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return ModelVersionRead.model_validate(mv)


@router.post("/{name}/versions/{version}/transition", response_model=ModelVersionRead)
async def transition_model_version(
    name: str,
    version: int,
    body: ModelTransitionRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")

    try:
        validate_transition(
            mv.current_stage,
            body.to_stage,
            actor_role=user.role.value,
            is_owner=(mv.owner_id == user.id),
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from_stage = mv.current_stage

    # Call MLflow
    client = _mlflow()
    archive = body.to_stage == ModelVersionStage.PRODUCTION
    try:
        await client.transition_model_version_stage(
            name=name, version=str(version), stage=body.to_stage.value,
            archive_existing_versions=archive,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MLflow transition failed: {e}")

    # Update local row
    mv.current_stage = body.to_stage
    mv.last_transitioned_at = datetime.now(timezone.utc)

    # If archiving existing Production, reflect in our table
    if archive:
        others = (await session.execute(
            select(ModelVersion).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.id != mv.id,
                ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
            )
        )).scalars().all()
        for o in others:
            # Log the implicit transition for audit
            session.add(ModelTransitionLog(
                model_version_id=o.id,
                from_stage=o.current_stage,
                to_stage=ModelVersionStage.ARCHIVED,
                actor_id=user.id,
                comment="auto-archived by transition to Production",
            ))
            o.current_stage = ModelVersionStage.ARCHIVED
            o.last_transitioned_at = datetime.now(timezone.utc)

    # Audit log
    session.add(ModelTransitionLog(
        model_version_id=mv.id,
        from_stage=from_stage,
        to_stage=body.to_stage,
        actor_id=user.id,
        comment=body.comment,
    ))

    await session.commit()
    await session.refresh(mv)
    return ModelVersionRead.model_validate(mv)


@router.delete("/{name}/versions/{version}", status_code=204)
async def delete_model_version(
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")
    if mv.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    if mv.current_stage not in (ModelVersionStage.NONE, ModelVersionStage.ARCHIVED):
        raise HTTPException(status_code=409, detail="must be stage=None or Archived")

    try:
        await _mlflow().delete_model_version(name, str(version))
    except Exception:
        pass  # best-effort; local deletion proceeds

    await session.delete(mv)
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 6: Write MLflow proxy tests**

Create `backend/tests/test_experiments_proxy.py`:

```python
import pytest
import respx
import httpx


@pytest.mark.asyncio
@respx.mock
async def test_experiments_list_proxied(user_client):
    respx.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/experiments/search").mock(
        return_value=httpx.Response(
            200,
            json={"experiments": [{"experiment_id": "1", "name": "detector:x:v1"}]},
        )
    )
    r = await user_client.get("/api/v1/experiments")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "detector:x:v1"


@pytest.mark.asyncio
@respx.mock
async def test_runs_list_for_experiment(user_client):
    respx.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/search").mock(
        return_value=httpx.Response(
            200,
            json={"runs": [{"info": {"run_id": "r1", "status": "FINISHED"}, "data": {}}]},
        )
    )
    r = await user_client.get("/api/v1/experiments/1/runs")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_run_proxied(user_client):
    respx.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
        return_value=httpx.Response(
            200,
            json={"run": {"info": {"run_id": "r1"}, "data": {"metrics": [], "params": []}}},
        )
    )
    r = await user_client.get("/api/v1/runs/r1")
    assert r.status_code == 200
    assert r.json()["info"]["run_id"] == "r1"


@pytest.mark.asyncio
@respx.mock
async def test_mlflow_error_proxied_as_502(user_client):
    respx.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
        return_value=httpx.Response(500, json={"error_code": "INTERNAL"}),
    )
    r = await user_client.get("/api/v1/runs/r1")
    assert r.status_code == 502
```

**Note:** For these tests, the `mock_mlflow` autouse fixture (Task 9) replaces `MlflowClient` with a stub. That stub won't go through respx. For proxy tests, we want the real MlflowClient, so disable the autouse fixture in this file:

```python
@pytest.fixture(autouse=True)
def _no_mock_mlflow():
    """Override the package-level autouse mock for proxy tests."""
    yield  # don't patch
```

Place this AFTER the `from` imports in `test_experiments_proxy.py`. Actually, this approach won't work because autouse fixtures cascade. Cleaner: make the autouse `mock_mlflow` opt-out via a marker. Modify it:

```python
@pytest.fixture(autouse=True)
def mock_mlflow(request, monkeypatch):
    if "no_mock_mlflow" in request.keywords:
        yield
        return
    # ... existing stub setup ...
    yield
```

And on proxy tests:

```python
@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
@respx.mock
async def test_experiments_list_proxied(user_client):
    ...
```

Register the marker in `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "no_mock_mlflow: do not apply the MLflow autouse mock",
]
```

- [ ] **Step 7: Implement MLflow proxy router**

Create `backend/app/routers/experiments_proxy.py`:

```python
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.deps import current_active_user
from app.models import User
from app.services.mlflow_client import MlflowClient, MlflowError

router = APIRouter()
logger = logging.getLogger(__name__)


def _client() -> MlflowClient:
    return MlflowClient(settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)


@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        return await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        return await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        return await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
    path: str | None = None,
):
    # MLflow artifact listing uses /api/2.0/mlflow/artifacts/list
    import httpx
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/artifacts/list"
    params = {"run_id": run_id}
    if path:
        params["path"] = path
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return r.json()


@router.get("/runs/{run_id}/artifacts/download")
async def download_artifact(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    """Stream an artifact file from MLflow's `/get-artifact` endpoint."""
    import httpx
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/get-artifact"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(url, params={"run_uuid": run_id, "path": path})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return Response(content=r.content, media_type="application/octet-stream")
```

- [ ] **Step 8: Register routers in main.py**

Edit `backend/app/main.py`:

```python
from app.routers import (
    admin, credentials, datasets, detectors, experiments_proxy,
    internal, jobs, models_registry,
)
```

And:

```python
app.include_router(
    models_registry.router,
    prefix="/api/v1/models",
    tags=["models"],
)
app.include_router(
    experiments_proxy.router,
    prefix="/api/v1",
    tags=["mlflow"],
)
```

- [ ] **Step 9: Run tests**

```bash
cd backend && uv run pytest tests/test_models_registry.py tests/test_experiments_proxy.py -v 2>&1 | tail -25
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add backend/app/routers/ backend/app/schemas/ backend/app/main.py backend/pyproject.toml backend/tests/
git commit -m "feat(backend): add model registry + MLflow proxy routers"
```

---

## Task 11: Reconciler Extension — Jobs + Model Sync

**Files:**
- Modify: `backend/app/reconciler.py`
- Create: `backend/tests/test_reconciler_jobs.py`

- [ ] **Step 1: Write failing reconciler tests**

Create `backend/tests/test_reconciler_jobs.py`:

```python
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Job
from app.models.job import JobStatus, JobType
from app.reconciler import reconcile_job


@pytest.mark.asyncio
async def test_reconcile_job_marks_running(db_session, seed_job):
    j = await seed_job(status=JobStatus.PREPARING)

    # Mock K8s: pod exists, running
    with _patched_k8s(pod_phase="Running", job_succeeded=None, job_failed=None):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.RUNNING
    assert j.started_at is not None


@pytest.mark.asyncio
async def test_reconcile_job_marks_succeeded_and_registers_model(
    db_session, seed_job, mlflow_stub
):
    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)
    with _patched_k8s(pod_phase=None, job_succeeded=1, job_failed=None):
        mlflow_stub.get_run.return_value = {
            "info": {"status": "FINISHED", "run_id": j.mlflow_run_id},
            "data": {
                "metrics": {"accuracy": 0.9, "f1": 0.85},
                "params": {},
                "tags": {},
            },
        }
        mlflow_stub.create_registered_model.return_value = {"name": "upxelfdet"}
        mlflow_stub.create_model_version.return_value = {
            "name": "upxelfdet",
            "version": "1",
            "run_id": j.mlflow_run_id,
        }
        await reconcile_job(db_session, j)

    await db_session.refresh(j)
    assert j.status == JobStatus.SUCCEEDED
    assert j.summary_metrics == {"accuracy": 0.9, "f1": 0.85}
    assert j.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_job_marks_failed(db_session, seed_job):
    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_k8s(pod_phase=None, job_succeeded=None, job_failed=1, exit_code=1):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.FAILED
    assert j.failure_reason == "detector_exit_nonzero"


@pytest.mark.asyncio
async def test_reconcile_job_marks_oom(db_session, seed_job):
    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_k8s(pod_phase=None, job_succeeded=None, job_failed=1, exit_code=137):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.failure_reason == "detector_oom"


@pytest.mark.asyncio
async def test_reconcile_job_timeout(db_session, seed_job, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "JOB_ACTIVE_DEADLINE_TRAIN_SECONDS", 1)
    j = await seed_job(
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc).replace(year=2020),
    )
    with _patched_k8s(pod_phase="Running", job_succeeded=None, job_failed=None):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.TIMEOUT


# -------- helpers --------

from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _patched_k8s(pod_phase, job_succeeded, job_failed, exit_code=0):
    class _S:
        succeeded = job_succeeded
        failed = job_failed
    class _Job:
        status = _S()
    class _Pod:
        class _Meta: name = "pod-xxx"
        metadata = _Meta()
        class _St:
            phase = pod_phase
            init_container_statuses = []
            container_statuses = [
                type("C", (), {
                    "name": "detector",
                    "state": type("T", (), {
                        "terminated": type("TT", (), {"exit_code": exit_code})()
                    })(),
                })()
            ] if job_failed else []
        status = _St()

    class _BatchStub:
        def read_namespaced_job(self, name, namespace, **kw):
            return _Job()
        def delete_namespaced_job(self, *a, **kw):
            pass

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            class _R: items = [_Pod()]
            return _R()
        def read_namespaced_pod_log(self, **kw):
            return "sample log tail"
        def delete_namespaced_secret(self, *a, **kw):
            pass

    with patch("app.reconciler.batch_v1", return_value=_BatchStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            yield


@pytest.fixture
async def mlflow_stub(monkeypatch):
    """Access the async stub MLflow client used by the reconciler."""
    stub = AsyncMock()
    def _factory(*a, **kw): return stub
    monkeypatch.setattr("app.reconciler.MlflowClient", _factory)
    return stub


@pytest.fixture
async def seed_job(db_session, seed_detector_version, seed_dataset, seed_user):
    async def _seed(
        status: JobStatus = JobStatus.PENDING,
        job_type: JobType = JobType.TRAIN,
        started_at=None,
    ):
        dv_id = await seed_detector_version()
        tr = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        te = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        j = Job(
            type=job_type,
            status=status,
            detector_version_id=uuid.UUID(dv_id),
            train_dataset_id=uuid.UUID(tr),
            test_dataset_id=uuid.UUID(te),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
            idempotency_key=uuid.uuid4().hex,
            token_hash="a" * 64,
            k8s_job_name=f"job-{job_type.value}-{uuid.uuid4().hex[:8]}",
            started_at=started_at,
        )
        db_session.add(j)
        await db_session.commit()
        await db_session.refresh(j)
        return j
    return _seed
```

- [ ] **Step 2: Confirm failing**

```bash
cd backend && uv run pytest tests/test_reconciler_jobs.py -v 2>&1 | tail -15
```

Expected: `AttributeError: module 'app.reconciler' has no attribute 'reconcile_job'`.

- [ ] **Step 3: Extend reconciler.py**

Edit `backend/app/reconciler.py` — add job handling alongside existing build handling. Append this section after the existing build reconciler code:

```python
# =============================================================================
# Phase 4: Job + Model Registry reconciliation
# =============================================================================

from app.models.job import Job, JobStatus, JobType, NON_TERMINAL_STATUSES
from app.services.job_spec import job_name as _job_name
from app.services.mlflow_client import MlflowClient


async def reconcile_job(session: AsyncSession, j: Job) -> None:
    """Poll K8s Job + MLflow state for a single job row, transition DB row."""
    if j.k8s_job_name is None:
        return

    # Get K8s Job
    try:
        k8s_job = batch_v1().read_namespaced_job(
            name=j.k8s_job_name, namespace=settings.JOB_NAMESPACE
        )
    except ApiException as e:
        if e.status == 404:
            j.status = JobStatus.FAILED
            j.failure_reason = "k8s_job_missing"
            j.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return

    # Timeout check
    if j.started_at is not None and _job_timed_out(j, k8s_job):
        try:
            batch_v1().delete_namespaced_job(
                name=j.k8s_job_name,
                namespace=settings.JOB_NAMESPACE,
                propagation_policy="Background",
            )
        except ApiException:
            pass
        j.status = JobStatus.TIMEOUT
        j.failure_reason = "detector_timeout"
        j.finished_at = datetime.now(timezone.utc)
        await session.commit()
        await _cleanup_job_secret(j)
        return

    if k8s_job.status.succeeded:
        await _handle_job_succeeded(session, j)
    elif k8s_job.status.failed:
        await _handle_job_failed(session, j)
    else:
        await _update_job_progress(session, j)


def _job_timed_out(j: Job, k8s_job) -> bool:
    deadline_map = {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }
    deadline = deadline_map.get(j.type, 3600)
    elapsed = (datetime.now(timezone.utc) - j.started_at.replace(tzinfo=timezone.utc)).total_seconds()
    return elapsed > deadline + 60


async def _update_job_progress(session: AsyncSession, j: Job) -> None:
    """Transition PREPARING → RUNNING once the detector container starts."""
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
    except ApiException:
        return
    if not pods.items:
        return
    pod = pods.items[0]
    if pod.status.phase == "Running" and j.status != JobStatus.RUNNING:
        j.status = JobStatus.RUNNING
        if j.started_at is None:
            j.started_at = datetime.now(timezone.utc)
        await session.commit()


async def _handle_job_succeeded(session: AsyncSession, j: Job) -> None:
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    run = await client.get_run(j.mlflow_run_id)
    metrics_raw = run["data"].get("metrics", {})
    # In MLflow REST, metrics may be list-of-dicts or dict; handle both
    if isinstance(metrics_raw, list):
        metrics = {m["key"]: m["value"] for m in metrics_raw}
    else:
        metrics = dict(metrics_raw)

    log_tail = await _capture_job_log_tail(j)

    j.summary_metrics = metrics
    j.log_tail = log_tail
    j.status = JobStatus.SUCCEEDED
    j.finished_at = datetime.now(timezone.utc)

    # Auto-register model for train jobs
    if j.type == JobType.TRAIN:
        try:
            await _register_model_from_job(session, client, j)
        except Exception:
            logger.exception("model registration failed for job %s", j.id)

    await session.commit()
    await _cleanup_job_secret(j)


async def _register_model_from_job(
    session: AsyncSession, client: MlflowClient, j: Job
) -> None:
    """Register a new MLflow model version + insert local ModelVersion row."""
    from app.models import Detector, DetectorVersion, ModelVersion
    from app.models.model_registry import ModelVersionStage

    dv = await session.get(DetectorVersion, j.detector_version_id)
    det = await session.get(Detector, dv.detector_id)
    name = det.name

    await client.create_registered_model(name)  # idempotent
    mv_resp = await client.create_model_version(
        name=name, source=f"runs:/{j.mlflow_run_id}/model", run_id=j.mlflow_run_id
    )
    mlflow_version = int(mv_resp["version"])

    mv = ModelVersion(
        mlflow_name=name,
        mlflow_version=mlflow_version,
        mlflow_run_id=j.mlflow_run_id,
        current_stage=ModelVersionStage.NONE,
        detector_version_id=j.detector_version_id,
        source_job_id=j.id,
        owner_id=j.owner_id,
    )
    session.add(mv)


async def _handle_job_failed(session: AsyncSession, j: Job) -> None:
    reason = await _extract_job_failure_reason(j)
    log_tail = await _capture_job_log_tail(j)
    j.status = JobStatus.FAILED
    j.failure_reason = reason
    j.log_tail = log_tail
    j.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await _cleanup_job_secret(j)


async def _extract_job_failure_reason(j: Job) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
    except ApiException:
        return "k8s_api_error"
    if not pods.items:
        return "pod_missing"
    pod = pods.items[0]

    for ic in (pod.status.init_container_statuses or []):
        if ic.state and ic.state.terminated and ic.state.terminated.exit_code not in (0, None):
            if ic.name == "model-fetcher":
                return "source_model_not_found"
            return f"init_{ic.name}_failed"

    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated:
            ec = cs.state.terminated.exit_code
            if ec == 137:
                return "detector_oom"
            if ec not in (0, None):
                return "detector_exit_nonzero"
    return "unknown_failure"


async def _capture_job_log_tail(j: Job) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
        if not pods.items:
            return ""
        pod = pods.items[0]
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.JOB_NAMESPACE,
            container="detector",
            tail_lines=200,
        )
        return log[-8192:]
    except ApiException:
        return ""


async def _cleanup_job_secret(j: Job) -> None:
    try:
        from app.services.job_spec import _job_token_secret_name
        core_v1().delete_namespaced_secret(
            name=_job_token_secret_name(j.id),
            namespace=settings.JOB_NAMESPACE,
        )
    except ApiException:
        pass


async def sync_model_versions(session: AsyncSession) -> None:
    """Pull latest stages from MLflow; reflect transitions initiated outside lolday."""
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    from app.models import ModelVersion
    from app.models.model_registry import ModelVersionStage

    # Get all local model versions
    all_local = (await session.execute(select(ModelVersion))).scalars().all()
    if not all_local:
        return

    # Query MLflow for all model versions (paginated)
    remote = await client.search_model_versions()
    by_key = {(m["name"], int(m["version"])): m for m in remote}

    for mv in all_local:
        rem = by_key.get((mv.mlflow_name, mv.mlflow_version))
        if rem is None:
            continue
        remote_stage = rem.get("current_stage", "None")
        try:
            stage_enum = ModelVersionStage(remote_stage)
        except ValueError:
            continue
        if stage_enum != mv.current_stage:
            mv.current_stage = stage_enum
            mv.last_transitioned_at = datetime.now(timezone.utc)
    await session.commit()
```

And modify the `reconciler_loop` to handle both:

```python
async def reconciler_loop(stop_event: asyncio.Event) -> None:
    logger.info("reconciler started (build + job)")
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        try:
            async with async_session_maker() as session:
                # Build reconcile pass (Phase 3)
                res_builds = await session.execute(
                    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
                )
                for b in res_builds.scalars().all():
                    try:
                        await reconcile_build(session, b)
                    except Exception:
                        logger.exception("reconcile_build failed", extra={"build_id": str(b.id)})

                # Job reconcile pass (Phase 4)
                res_jobs = await session.execute(
                    select(Job).where(Job.status.in_(NON_TERMINAL_STATUSES))
                )
                for j in res_jobs.scalars().all():
                    try:
                        await reconcile_job(session, j)
                    except Exception:
                        logger.exception("reconcile_job failed", extra={"job_id": str(j.id)})

                # Model version sync every 6 iterations (≈ 1 minute at 10s period)
                if iteration % 6 == 0:
                    try:
                        await sync_model_versions(session)
                    except Exception:
                        logger.exception("sync_model_versions failed")
        except Exception:
            logger.exception("reconciler iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
    logger.info("reconciler stopped")
```

- [ ] **Step 4: Run reconciler tests**

```bash
cd backend && uv run pytest tests/test_reconciler_jobs.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```bash
cd backend && uv run pytest -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/reconciler.py backend/tests/test_reconciler_jobs.py
git commit -m "feat(backend): extend reconciler for job lifecycle + model sync"
```

---

## Task 12: Job Helper Image (write_config + fetch_model)

**Files:**
- Create: `charts/lolday/helpers/job-helper/Dockerfile`
- Create: `charts/lolday/helpers/job-helper/pyproject.toml`
- Create: `charts/lolday/helpers/job-helper/job_helper/__init__.py`
- Create: `charts/lolday/helpers/job-helper/job_helper/write_config.py`
- Create: `charts/lolday/helpers/job-helper/job_helper/fetch_model.py`
- Create: `charts/lolday/helpers/job-helper/tests/test_helpers.py`

- [ ] **Step 1: Scaffold package**

Create directory: `charts/lolday/helpers/job-helper/`.

Create `charts/lolday/helpers/job-helper/pyproject.toml`:

```toml
[project]
name = "job-helper"
version = "0.1.0"
description = "Lolday job pod init container helpers"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.0",
    "mlflow-skinny>=2.20.0",
]

[dependency-groups]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.25.0", "respx>=0.21.0"]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.hatch.build.targets.wheel]
packages = ["job_helper"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Create `charts/lolday/helpers/job-helper/job_helper/__init__.py`:

```python
"""Lolday job pod init-container helpers.

- write_config: fetches resolved_config + dataset CSVs from backend, writes to /mnt/config
- fetch_model: downloads an MLflow run's model artifacts to /mnt/source-model
"""
```

- [ ] **Step 2: Write tests**

Create `charts/lolday/helpers/job-helper/tests/__init__.py` (empty).

Create `charts/lolday/helpers/job-helper/tests/test_helpers.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
import respx


@pytest.mark.asyncio
@respx.mock
async def test_write_config_writes_all_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_ID", "aabbccdd-0000-0000-0000-000000000000")
    monkeypatch.setenv("BACKEND_URL", "http://backend")
    monkeypatch.setenv("JOB_TOKEN", "mytoken")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    respx.get(
        "http://backend/api/v1/internal/jobs/aabbccdd-0000-0000-0000-000000000000/config"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "config": {"data": {"train": "/mnt/config/train.csv"}},
                "train_csv": "file_name,label\naaa,Malware\n",
                "test_csv": "file_name,label\nbbb,Benign\n",
                "predict_csv": None,
            },
        )
    )

    from job_helper import write_config
    await write_config.main()

    cfg = json.loads((config_dir / "config.json").read_text())
    assert cfg["data"]["train"] == "/mnt/config/train.csv"

    train = (config_dir / "train.csv").read_text()
    assert "aaa,Malware" in train

    test = (config_dir / "test.csv").read_text()
    assert "bbb,Benign" in test

    # predict.csv must NOT be written when source is None
    assert not (config_dir / "predict.csv").exists()


@pytest.mark.asyncio
@respx.mock
async def test_write_config_retries_on_500(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_ID", "aabbccdd-0000-0000-0000-000000000000")
    monkeypatch.setenv("BACKEND_URL", "http://backend")
    monkeypatch.setenv("JOB_TOKEN", "mytoken")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    call_count = 0
    def _maybe_fail(request):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={
            "config": {"x": 1}, "train_csv": None, "test_csv": None, "predict_csv": None,
        })

    respx.get(
        "http://backend/api/v1/internal/jobs/aabbccdd-0000-0000-0000-000000000000/config"
    ).mock(side_effect=_maybe_fail)

    from job_helper import write_config
    await write_config.main()
    assert call_count == 3
    assert (config_dir / "config.json").read_text()


def test_fetch_model_downloads_artifacts(tmp_path, monkeypatch):
    """Verify fetch_model uses mlflow-skinny's download_artifacts correctly."""
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow")
    monkeypatch.setenv("SOURCE_RUN_ID", "run123")
    monkeypatch.setenv("ARTIFACT_PATH", "model")
    target = tmp_path / "source-model"
    target.mkdir()
    monkeypatch.setenv("TARGET_DIR", str(target))

    # Stub MLflow download
    from unittest.mock import patch, MagicMock

    def _fake_download(run_id, path, dst_path):
        # Simulate: write a couple of files
        d = Path(dst_path) / path
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.pkl").write_bytes(b"binary")
        (d / "label_encoder.pkl").write_bytes(b"binary")
        return str(d)

    with patch("mlflow.artifacts.download_artifacts", side_effect=_fake_download):
        from job_helper import fetch_model
        fetch_model.main()

    assert (target / "model" / "model.pkl").exists()
    assert (target / "model" / "label_encoder.pkl").exists()
```

- [ ] **Step 3: Confirm failing**

```bash
cd charts/lolday/helpers/job-helper && uv sync && uv run pytest -v 2>&1 | tail -15
```

Expected: ModuleNotFoundError.

- [ ] **Step 4: Implement write_config**

Create `charts/lolday/helpers/job-helper/job_helper/write_config.py`:

```python
"""Init container: fetch resolved config + CSVs from backend, write to /mnt/config."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx


async def main() -> None:
    job_id = os.environ["JOB_ID"]
    backend = os.environ["BACKEND_URL"].rstrip("/")
    token = os.environ["JOB_TOKEN"]
    config_dir = Path(os.environ.get("CONFIG_DIR", "/mnt/config"))
    config_dir.mkdir(parents=True, exist_ok=True)

    url = f"{backend}/api/v1/internal/jobs/{job_id}/config"
    headers = {"Authorization": f"Bearer {token}"}

    # Retry up to 5 times with exponential backoff (allows backend rolling restart)
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                break
            if r.status_code in (401, 403, 404):
                # Auth/identity errors are fatal
                print(f"fatal: backend returned {r.status_code}: {r.text}", file=sys.stderr)
                sys.exit(2)
            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text}")
        except httpx.HTTPError as e:
            last_err = e
        await asyncio.sleep(2 ** attempt)
    else:
        print(f"fatal: backend unreachable after 5 attempts: {last_err!r}", file=sys.stderr)
        sys.exit(3)

    # Write config.json
    (config_dir / "config.json").write_text(json.dumps(data["config"], indent=2))

    # Write CSV files (only for non-null fields)
    csv_map = {
        "train_csv": "train.csv",
        "test_csv": "test.csv",
        "predict_csv": "predict.csv",
    }
    for key, filename in csv_map.items():
        content = data.get(key)
        if content is not None:
            (config_dir / filename).write_text(content)

    print(f"config written to {config_dir}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Implement fetch_model**

Create `charts/lolday/helpers/job-helper/job_helper/fetch_model.py`:

```python
"""Init container: download MLflow run artifacts to /mnt/source-model."""

import os
import sys
from pathlib import Path

import mlflow.artifacts


def main() -> None:
    os.environ.setdefault("MLFLOW_TRACKING_URI", "http://mlflow.lolday.svc:5000")
    run_id = os.environ["SOURCE_RUN_ID"]
    artifact_path = os.environ.get("ARTIFACT_PATH", "model")
    target = Path(os.environ.get("TARGET_DIR", "/mnt/source-model"))
    target.mkdir(parents=True, exist_ok=True)

    try:
        mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path=artifact_path,
            dst_path=str(target),
        )
    except Exception as e:
        print(f"fatal: artifact download failed: {e!r}", file=sys.stderr)
        sys.exit(4)

    print(f"downloaded run {run_id}:{artifact_path} to {target}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests**

```bash
cd charts/lolday/helpers/job-helper && uv run pytest -v 2>&1 | tail -15
```

Expected: all pass.

- [ ] **Step 7: Write Dockerfile**

Create `charts/lolday/helpers/job-helper/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml /app/
COPY job_helper/ /app/job_helper/

RUN pip install --no-cache-dir .

USER 1000

# No ENTRYPOINT: init containers specify `command` in the Pod spec.
```

- [ ] **Step 8: Commit**

```bash
git add charts/lolday/helpers/job-helper/
git commit -m "feat(helpers): add job-helper init-container image (write_config + fetch_model)"
```

---

## Task 13: Helm — Sample PVs + PVCs

**Files:**
- Create: `charts/lolday/templates/samples-pv.yaml`
- Create: `charts/lolday/templates/samples-pvc.yaml`
- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Add samples block to values.yaml**

Edit `charts/lolday/values.yaml`, add:

```yaml
samples:
  malware:
    enabled: true
    hostPath: /data/malware-samples
    storage: 500Gi
    nodeHostname: server30
  benign:
    enabled: true
    hostPath: /data/benign-samples
    storage: 100Gi
    nodeHostname: server30
```

- [ ] **Step 2: Write PV template**

Create `charts/lolday/templates/samples-pv.yaml`:

```yaml
{{- if .Values.samples.malware.enabled }}
apiVersion: v1
kind: PersistentVolume
metadata:
  name: malware-samples
  labels:
    app.kubernetes.io/name: malware-samples
    app.kubernetes.io/managed-by: {{ .Release.Service }}
spec:
  capacity:
    storage: {{ .Values.samples.malware.storage }}
  accessModes: [ReadOnlyMany]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  hostPath:
    path: {{ .Values.samples.malware.hostPath }}
    type: Directory
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: [{{ .Values.samples.malware.nodeHostname }}]
{{- end }}
{{- if .Values.samples.benign.enabled }}
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: benign-samples
  labels:
    app.kubernetes.io/name: benign-samples
    app.kubernetes.io/managed-by: {{ .Release.Service }}
spec:
  capacity:
    storage: {{ .Values.samples.benign.storage }}
  accessModes: [ReadOnlyMany]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  hostPath:
    path: {{ .Values.samples.benign.hostPath }}
    type: Directory
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: [{{ .Values.samples.benign.nodeHostname }}]
{{- end }}
```

- [ ] **Step 3: Write PVC template**

Create `charts/lolday/templates/samples-pvc.yaml`:

```yaml
{{- if .Values.samples.malware.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: malware-samples
  namespace: {{ .Release.Namespace }}
spec:
  accessModes: [ReadOnlyMany]
  storageClassName: ""
  volumeName: malware-samples
  resources:
    requests:
      storage: {{ .Values.samples.malware.storage }}
{{- end }}
{{- if .Values.samples.benign.enabled }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: benign-samples
  namespace: {{ .Release.Namespace }}
spec:
  accessModes: [ReadOnlyMany]
  storageClassName: ""
  volumeName: benign-samples
  resources:
    requests:
      storage: {{ .Values.samples.benign.storage }}
{{- end }}
```

- [ ] **Step 4: Validate Helm render**

```bash
cd charts/lolday && helm template lolday . --set samples.malware.enabled=true --set samples.benign.enabled=true | grep -A 30 "kind: PersistentVolume" | head -60
```

Expected: two PV definitions with correct `hostPath` and `nodeAffinity`.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/samples-pv.yaml charts/lolday/templates/samples-pvc.yaml charts/lolday/values.yaml
git commit -m "feat(chart): add sample PVs (hostPath) + PVCs for malware + benign"
```

---

## Task 14: Helm — MLflow Deployment + PVC + Service + DB Init Hook

**Files:**
- Create: `charts/lolday/templates/mlflow.yaml`
- Create: `charts/lolday/templates/mlflow-db-init-job.yaml`
- Create: `charts/lolday/templates/mlflow-secret.yaml`
- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Add mlflow block to values.yaml**

Edit `charts/lolday/values.yaml`, add:

```yaml
mlflow:
  enabled: true
  image: ghcr.io/mlflow/mlflow:v2.20.3
  storage: 100Gi
  storageClassName: local-path
  service:
    port: 5000
  resources:
    requests: { cpu: 200m, memory: 512Mi }
    limits:   { cpu: 2, memory: 4Gi }
  # DB password injected via --set at deploy time (NEVER commit):
  db:
    username: mlflow
    database: mlflow
    password: ""
```

- [ ] **Step 2: Write mlflow-secret.yaml**

Create `charts/lolday/templates/mlflow-secret.yaml`:

```yaml
{{- if .Values.mlflow.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: mlflow-db
  namespace: {{ .Release.Namespace }}
type: Opaque
stringData:
  username: {{ .Values.mlflow.db.username | quote }}
  password: {{ required "mlflow.db.password must be set via --set" .Values.mlflow.db.password | quote }}
  database: {{ .Values.mlflow.db.database | quote }}
{{- end }}
```

- [ ] **Step 3: Write mlflow-db-init-job.yaml**

Create `charts/lolday/templates/mlflow-db-init-job.yaml`:

```yaml
{{- if .Values.mlflow.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: mlflow-db-init-{{ .Release.Revision }}
  namespace: {{ .Release.Namespace }}
  annotations:
    helm.sh/hook: post-install,post-upgrade
    helm.sh/hook-weight: "5"
    helm.sh/hook-delete-policy: hook-succeeded,before-hook-creation
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: OnFailure
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        fsGroup: 999
      containers:
        - name: init
          image: postgres:16
          command: [sh, -c]
          args:
            - |
              set -e
              export PGPASSWORD="$PG_ADMIN_PASSWORD"
              # Create DB if missing
              DB_EXISTS=$(psql -h postgresql.{{ .Release.Namespace }}.svc -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$MLFLOW_DB'" || true)
              if [ "$DB_EXISTS" != "1" ]; then
                psql -h postgresql.{{ .Release.Namespace }}.svc -U postgres -c "CREATE DATABASE \"$MLFLOW_DB\""
              fi
              # Create user + grants (idempotent)
              psql -h postgresql.{{ .Release.Namespace }}.svc -U postgres <<SQL
              DO \$\$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$MLFLOW_USER') THEN
                  CREATE USER "$MLFLOW_USER" WITH PASSWORD '$MLFLOW_PASSWORD';
                END IF;
              END \$\$;
              GRANT ALL PRIVILEGES ON DATABASE "$MLFLOW_DB" TO "$MLFLOW_USER";
              SQL
          env:
            - name: PG_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: lolday-postgresql
                  key: postgres-password
            - name: MLFLOW_USER
              valueFrom: { secretKeyRef: { name: mlflow-db, key: username } }
            - name: MLFLOW_PASSWORD
              valueFrom: { secretKeyRef: { name: mlflow-db, key: password } }
            - name: MLFLOW_DB
              valueFrom: { secretKeyRef: { name: mlflow-db, key: database } }
{{- end }}
```

**Check:** the secret name for the main postgres admin is `lolday-postgresql` per Phase 2; verify this matches the current release. If different, update the `secretKeyRef.name`.

- [ ] **Step 4: Write mlflow.yaml (Deployment + PVC + Service)**

Create `charts/lolday/templates/mlflow.yaml`:

```yaml
{{- if .Values.mlflow.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mlflow-artifacts
  namespace: {{ .Release.Namespace }}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: {{ .Values.mlflow.storageClassName | quote }}
  resources:
    requests:
      storage: {{ .Values.mlflow.storage }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mlflow
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/component: mlflow
    app.kubernetes.io/name: mlflow
spec:
  replicas: 1
  strategy: { type: Recreate }
  selector:
    matchLabels:
      app.kubernetes.io/component: mlflow
  template:
    metadata:
      labels:
        app.kubernetes.io/component: mlflow
        app.kubernetes.io/name: mlflow
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
        - name: mlflow
          image: {{ .Values.mlflow.image | quote }}
          imagePullPolicy: IfNotPresent
          command: [mlflow, server]
          args:
            - --host=0.0.0.0
            - --port={{ .Values.mlflow.service.port }}
            - --backend-store-uri=postgresql+psycopg2://$(PG_USER):$(PG_PASSWORD)@postgresql.{{ .Release.Namespace }}.svc:5432/$(PG_DB)
            - --default-artifact-root=/mlflow-artifacts
            - --serve-artifacts
          env:
            - name: PG_USER
              valueFrom: { secretKeyRef: { name: mlflow-db, key: username } }
            - name: PG_PASSWORD
              valueFrom: { secretKeyRef: { name: mlflow-db, key: password } }
            - name: PG_DB
              valueFrom: { secretKeyRef: { name: mlflow-db, key: database } }
          ports:
            - containerPort: {{ .Values.mlflow.service.port }}
              name: http
          volumeMounts:
            - name: artifacts
              mountPath: /mlflow-artifacts
          resources:
            {{- toYaml .Values.mlflow.resources | nindent 12 }}
          readinessProbe:
            httpGet: { path: /health, port: http }
            initialDelaySeconds: 15
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /health, port: http }
            initialDelaySeconds: 45
            periodSeconds: 15
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
      volumes:
        - name: artifacts
          persistentVolumeClaim:
            claimName: mlflow-artifacts
---
apiVersion: v1
kind: Service
metadata:
  name: mlflow
  namespace: {{ .Release.Namespace }}
  labels:
    app.kubernetes.io/component: mlflow
spec:
  selector:
    app.kubernetes.io/component: mlflow
  ports:
    - port: {{ .Values.mlflow.service.port }}
      targetPort: http
      name: http
{{- end }}
```

- [ ] **Step 5: Validate Helm render**

```bash
cd charts/lolday && helm template lolday . --set mlflow.db.password=testpw | grep -A 5 "kind: Deployment" | head -30
```

Expected: MLflow Deployment rendered.

```bash
helm template lolday . --set mlflow.db.password=testpw | grep -A 20 "mlflow-db-init"
```

Expected: post-install hook Job rendered.

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/templates/mlflow.yaml charts/lolday/templates/mlflow-db-init-job.yaml charts/lolday/templates/mlflow-secret.yaml charts/lolday/values.yaml
git commit -m "feat(chart): add MLflow server deployment + PVC + DB init hook"
```

---

## Task 15: Helm — Job NetworkPolicy + Backend Env + RBAC

**Files:**
- Create: `charts/lolday/templates/job-networkpolicy.yaml`
- Modify: `charts/lolday/templates/backend.yaml`
- Modify: `charts/lolday/templates/backend-rbac.yaml`
- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Add jobs block to values.yaml**

Edit `charts/lolday/values.yaml`, add:

```yaml
jobs:
  helperImage: harbor.harbor.svc:80/lolday/job-helper:v1
  activeDeadlineSeconds:
    train: 21600
    evaluate: 1800
    predict: 3600
  perUserConcurrency: 2
  idempotencyWindowSeconds: 300
  networkPolicy:
    enabled: true
```

And under `backend.env`:

```yaml
backend:
  env:
    # ... existing Phase 3 env ...
    MLFLOW_TRACKING_URI: http://mlflow.lolday.svc:5000
    DATASET_CSV_MAX_BYTES: "10485760"
    JOB_HELPER_IMAGE: "harbor.harbor.svc:80/lolday/job-helper:v1"
    JOB_PER_USER_CONCURRENCY: "2"
    JOB_IDEMPOTENCY_WINDOW_SECONDS: "300"
    JOB_BACKEND_URL: "http://backend.lolday.svc:8000"
    SAMPLES_ROOT: "/mnt/samples"
    SAMPLES_LOCAL_ROOT: "/data"
    JOB_NODE_SELECTOR_HOSTNAME: "server30"
```

- [ ] **Step 2: Write job NetworkPolicy**

Create `charts/lolday/templates/job-networkpolicy.yaml`:

```yaml
{{- if .Values.jobs.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lolday-job-egress
  namespace: {{ .Release.Namespace }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: lolday-job
  policyTypes: [Ingress, Egress]
  ingress: []
  egress:
    # DNS
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - { protocol: UDP, port: 53 }
        - { protocol: TCP, port: 53 }
    # MLflow (detector container)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: mlflow
      ports:
        - { protocol: TCP, port: 5000 }
    # Backend (config-writer init container)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Release.Namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports:
        - { protocol: TCP, port: 8000 }
{{- end }}
```

- [ ] **Step 3: Extend backend RBAC**

Edit `charts/lolday/templates/backend-rbac.yaml`. Add to the existing Role's rules (if not already present — Phase 3 should have most):

```yaml
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [pods/log]
    verbs: [get, list]
```

Phase 3 already grants jobs/secrets/configmaps verbs; verify by reading the existing file first.

- [ ] **Step 4: Add MLflow-aware env to backend Deployment**

Edit `charts/lolday/templates/backend.yaml`, look for the `env:` section under the backend container. Ensure these new vars are added (if not already defined via `values.backend.env`):

```yaml
        - name: MLFLOW_TRACKING_URI
          value: {{ .Values.backend.env.MLFLOW_TRACKING_URI | quote }}
        - name: DATASET_CSV_MAX_BYTES
          value: {{ .Values.backend.env.DATASET_CSV_MAX_BYTES | quote }}
        - name: JOB_HELPER_IMAGE
          value: {{ .Values.backend.env.JOB_HELPER_IMAGE | quote }}
        - name: JOB_PER_USER_CONCURRENCY
          value: {{ .Values.backend.env.JOB_PER_USER_CONCURRENCY | quote }}
        - name: JOB_IDEMPOTENCY_WINDOW_SECONDS
          value: {{ .Values.backend.env.JOB_IDEMPOTENCY_WINDOW_SECONDS | quote }}
        - name: JOB_BACKEND_URL
          value: {{ .Values.backend.env.JOB_BACKEND_URL | quote }}
        - name: SAMPLES_ROOT
          value: {{ .Values.backend.env.SAMPLES_ROOT | quote }}
        - name: SAMPLES_LOCAL_ROOT
          value: {{ .Values.backend.env.SAMPLES_LOCAL_ROOT | quote }}
        - name: JOB_NODE_SELECTOR_HOSTNAME
          value: {{ .Values.backend.env.JOB_NODE_SELECTOR_HOSTNAME | quote }}
```

If the Phase 3 backend.yaml already uses a loop like `{{ range $k, $v := .Values.backend.env }}`, no manual addition needed — `values.yaml` populates it.

- [ ] **Step 5: Validate Helm render**

```bash
cd charts/lolday && helm template lolday . --set mlflow.db.password=x | grep -A 10 "lolday-job-egress"
```

Expected: NetworkPolicy rendered.

```bash
helm template lolday . --set mlflow.db.password=x | grep MLFLOW_TRACKING_URI
```

Expected: env var present on backend Deployment.

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/templates/ charts/lolday/values.yaml
git commit -m "feat(chart): add job NetworkPolicy + backend MLflow/jobs env"
```

---

## Task 16: maldet Framework PR — MLflow Integration

**Repo:** `islab-malware-detector` (upstream on GitHub; user owns it)

**Files in that repo:**
- Modify: `pyproject.toml`
- Modify: `src/maldet/cli.py`
- Create: `tests/test_cli_mlflow.py`
- Update: `README.md` (brief)

This task happens in a separate clone of `islab-malware-detector`. Treat as a distinct PR. Commit + push + open PR from a feature branch.

- [ ] **Step 1: Clone + branch**

```bash
cd /home/bolin8017/Documents/repositories/islab-malware-detector
git fetch origin && git checkout main && git pull
git checkout -b feat/mlflow-tracking
```

- [ ] **Step 2: Add optional MLflow dependency**

Edit `pyproject.toml`:

```toml
[project.optional-dependencies]
mlflow = [
    "mlflow-skinny>=2.20.0",
]
dev = [
    # ... existing ...
    "mlflow-skinny>=2.20.0",   # for running test_cli_mlflow.py
]
```

Bump version:

```toml
[project]
name = "islab-malware-detector"
version = "0.5.0"
```

- [ ] **Step 3: Add MLflow helpers to maldet.cli**

Edit `src/maldet/cli.py`. Add imports at the top:

```python
import functools
import json
import os
from contextlib import nullcontext
from pathlib import Path
```

Add helper functions (before `build_cli`):

```python
def _mlflow_enabled() -> bool:
    """True iff MLFLOW_TRACKING_URI is set; optional mlflow import attempted lazily."""
    return bool(os.getenv("MLFLOW_TRACKING_URI"))


def _maybe_mlflow_run():
    """Return an active MLflow run context or a no-op context manager."""
    if not _mlflow_enabled():
        return nullcontext()
    try:
        import mlflow
    except ImportError:
        return nullcontext()
    run_id = os.getenv("MLFLOW_RUN_ID")
    if run_id:
        return mlflow.start_run(run_id=run_id)
    return mlflow.start_run()


def _flatten_config(cfg) -> dict:
    """Collapse Pydantic config into MLflow-friendly flat params (keys <= 250)."""
    try:
        d = cfg.model_dump(mode="json")
    except AttributeError:
        d = dict(cfg)

    out: dict = {}

    def walk(prefix: str, v) -> None:
        if isinstance(v, dict):
            for k, vv in v.items():
                new = f"{prefix}.{k}" if prefix else k
                walk(new, vv)
        elif isinstance(v, (list, tuple)):
            out[prefix] = json.dumps(v)
        else:
            out[prefix] = v

    walk("", d)
    return out


def _log_common_to_mlflow(cfg, action: str) -> None:
    """Call at the start of each command when MLflow is active."""
    if not _mlflow_enabled():
        return
    try:
        import mlflow
    except ImportError:
        return

    mlflow.set_tag("maldet.action", action)
    try:
        mlflow.log_dict(cfg.model_dump(mode="json"), "config.json")
    except Exception:
        pass

    for k, v in _flatten_config(cfg).items():
        try:
            mlflow.log_param(k[:250], str(v)[:500])
        except Exception:
            pass  # param already logged by MLflow's own autolog

    try:
        mlflow.autolog(log_models=False, silent=True)
    except Exception:
        pass
```

Replace the `train`, `evaluate`, `predict` command bodies:

```python
    @app.command()
    def train(
        config: Annotated[
            Path | None,
            typer.Option("--config", "-c", help="Path to config file"),
        ] = None,
        log_level: Annotated[
            str,
            typer.Option("--log-level", "-l", help="Logging level"),
        ] = "INFO",
        log_format: Annotated[
            str,
            typer.Option("--log-format", help="Logging format"),
        ] = "console",
    ) -> None:
        """Train the detector model."""
        configure_logging(level=log_level, format=log_format)
        cfg = config_class.from_file(config) if config else config_class()

        with _maybe_mlflow_run():
            _log_common_to_mlflow(cfg, "train")
            detector = detector_class(cfg)
            model_path = detector.train()

            if _mlflow_enabled():
                try:
                    import mlflow
                    if model_path and Path(model_path).exists():
                        mlflow.log_artifacts(str(model_path), artifact_path="model")
                except Exception:
                    pass

        typer.echo(f"Model saved to {model_path}")

    @app.command()
    def evaluate(
        config: Annotated[
            Path | None,
            typer.Option("--config", "-c", help="Path to config file"),
        ] = None,
        log_level: Annotated[
            str,
            typer.Option("--log-level", "-l", help="Logging level"),
        ] = "INFO",
        log_format: Annotated[
            str,
            typer.Option("--log-format", help="Logging format"),
        ] = "console",
    ) -> None:
        """Evaluate the detector on test data."""
        configure_logging(level=log_level, format=log_format)
        cfg = config_class.from_file(config) if config else config_class()

        with _maybe_mlflow_run():
            _log_common_to_mlflow(cfg, "evaluate")
            detector = detector_class(cfg)
            metrics = detector.evaluate()

            # Always write metrics.json for platform consumption
            log_dir = Path(cfg.output.log)
            log_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = log_dir / "metrics.json"
            try:
                metrics_path.write_text(json.dumps(metrics, default=str, indent=2))
            except Exception:
                pass

            if _mlflow_enabled():
                try:
                    import mlflow
                    numeric = {
                        k: float(v)
                        for k, v in metrics.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
                    if numeric:
                        mlflow.log_metrics(numeric)
                    if metrics_path.exists():
                        mlflow.log_artifact(str(metrics_path))
                except Exception:
                    pass

        typer.echo("Evaluation Results:")
        for k, v in metrics.items():
            typer.echo(f"  {k}: {v}")

    @app.command()
    def predict(
        config: Annotated[
            Path | None,
            typer.Option("--config", "-c", help="Path to config file"),
        ] = None,
        log_level: Annotated[
            str,
            typer.Option("--log-level", "-l", help="Logging level"),
        ] = "INFO",
        log_format: Annotated[
            str,
            typer.Option("--log-format", help="Logging format"),
        ] = "console",
    ) -> None:
        """Run prediction on input data."""
        configure_logging(level=log_level, format=log_format)
        cfg = config_class.from_file(config) if config else config_class()

        with _maybe_mlflow_run():
            _log_common_to_mlflow(cfg, "predict")
            detector = detector_class(cfg)
            output_path = detector.predict()

            if _mlflow_enabled():
                try:
                    import mlflow
                    if output_path and Path(output_path).exists():
                        mlflow.log_artifact(str(output_path), artifact_path="prediction")
                except Exception:
                    pass

        typer.echo(f"Predictions saved to {output_path}")
```

- [ ] **Step 4: Write MLflow-integration tests**

Create `tests/test_cli_mlflow.py`:

```python
"""Verify MLflow integration in maldet.cli is correctly gated and well-behaved."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maldet.cli import build_cli
from tests.conftest import MinimalDetector  # fixture detector class with stub train/eval/predict


runner = CliRunner()


@pytest.fixture
def app():
    return build_cli(MinimalDetector, MinimalDetector.config_class)


def test_train_runs_without_mlflow_env(app, tmp_path, monkeypatch):
    """Without MLFLOW_TRACKING_URI, detector runs normally."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"output": {"model": str(tmp_path / "m")}}))
    result = runner.invoke(app, ["train", "--config", str(cfg)])
    assert result.exit_code == 0


def test_train_logs_to_mlflow_when_enabled(app, tmp_path, monkeypatch):
    """With MLFLOW_TRACKING_URI + MLFLOW_RUN_ID, mlflow.start_run is called."""
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://fake")
    monkeypatch.setenv("MLFLOW_RUN_ID", "run-xyz")

    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"output": {"model": str(tmp_path / "m")}}))

    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=None)

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        result = runner.invoke(app, ["train", "--config", str(cfg)])

    assert result.exit_code == 0
    mock_mlflow.start_run.assert_called_once_with(run_id="run-xyz")
    # At least one log_param call (for config flattening)
    assert mock_mlflow.log_param.call_count > 0


def test_evaluate_always_writes_metrics_json(app, tmp_path, monkeypatch):
    """Even without MLflow, evaluate should leave metrics.json behind."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    cfg = tmp_path / "c.json"
    log_dir = tmp_path / "logs"
    cfg.write_text(json.dumps({"output": {"log": str(log_dir)}}))
    result = runner.invoke(app, ["evaluate", "--config", str(cfg)])
    assert result.exit_code == 0
    assert (log_dir / "metrics.json").exists()


def test_evaluate_logs_metrics_to_mlflow(app, tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://fake")
    monkeypatch.setenv("MLFLOW_RUN_ID", "run-abc")

    cfg = tmp_path / "c.json"
    log_dir = tmp_path / "logs"
    cfg.write_text(json.dumps({"output": {"log": str(log_dir)}}))

    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=None)

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        result = runner.invoke(app, ["evaluate", "--config", str(cfg)])
    assert result.exit_code == 0
    mock_mlflow.log_metrics.assert_called()  # at least once


def test_non_numeric_metrics_excluded_from_mlflow(app, tmp_path, monkeypatch):
    """MLflow metrics must be numeric; detector may return dicts / lists."""
    # This test depends on MinimalDetector.evaluate returning a mix of types.
    # See tests/conftest.py — MinimalDetector.evaluate returns:
    #   {"accuracy": 0.9, "confusion_matrix": [[10, 1], [0, 8]], "model": "SVM"}
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://fake")
    monkeypatch.setenv("MLFLOW_RUN_ID", "run-abc")

    cfg = tmp_path / "c.json"
    log_dir = tmp_path / "logs"
    cfg.write_text(json.dumps({"output": {"log": str(log_dir)}}))

    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=None)

    with patch.dict("sys.modules", {"mlflow": mock_mlflow}):
        result = runner.invoke(app, ["evaluate", "--config", str(cfg)])

    assert result.exit_code == 0
    call_args = mock_mlflow.log_metrics.call_args
    logged_metrics = call_args[0][0] if call_args else {}
    # Only numeric keys:
    assert "accuracy" in logged_metrics
    assert "confusion_matrix" not in logged_metrics
    assert "model" not in logged_metrics
```

Need a minimal fixture detector. Check `tests/conftest.py` — if a `MinimalDetector` doesn't exist, add one:

```python
# tests/conftest.py (additions)
from pathlib import Path
from typing import Any

from maldet import BaseDetector, BaseDetectorConfig


class MinimalConfig(BaseDetectorConfig):
    """Bare config. Omits detector-specific nested configs for simplicity."""


class MinimalDetector(BaseDetector):
    config_class = MinimalConfig

    def train(self) -> Path:
        p = Path(self.config.output.model)
        p.mkdir(parents=True, exist_ok=True)
        (p / "dummy.pkl").write_bytes(b"x")
        return p

    def evaluate(self) -> dict[str, Any]:
        return {
            "accuracy": 0.9,
            "f1": 0.85,
            "confusion_matrix": [[10, 1], [0, 8]],
            "model": "SVM",
        }

    def predict(self) -> Path:
        p = Path(self.config.output.prediction)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("file_name,prediction\naaa,Malware\n")
        return p
```

- [ ] **Step 5: Run maldet tests**

```bash
cd /home/bolin8017/Documents/repositories/islab-malware-detector && uv sync --extra dev && uv run pytest -v 2>&1 | tail -20
```

Expected: existing tests still pass + new MLflow tests pass.

- [ ] **Step 6: Update README briefly**

Edit `README.md`, add under "Advanced" or a new "Tracking" section:

```markdown
### MLflow Tracking

Detectors auto-log to MLflow when `MLFLOW_TRACKING_URI` is set in the
environment. When unset, behavior is unchanged.

Install with the optional `mlflow` extra:
```

```
pip install "islab-malware-detector[mlflow]"
```

```markdown
Environment variables:

| Variable                | Effect |
|-------------------------|--------|
| `MLFLOW_TRACKING_URI`   | When set, enables MLflow tracking |
| `MLFLOW_RUN_ID`         | Reuse an existing run (lolday creates it) |
| `MLFLOW_MODEL_NAME`     | Name used when registering models (default: detector class name) |

Logged artifacts per action:
- `train`: flattened config params, `config.json`, model directory under `model/`
- `evaluate`: numeric metrics, `metrics.json`
- `predict`: prediction file under `prediction/`

Detectors that don't opt into the `mlflow` extra still work: the tracking
code gracefully no-ops on import failure.
```

- [ ] **Step 7: Commit + push + open PR**

```bash
git add pyproject.toml src/maldet/cli.py tests/ README.md
git commit -m "feat: add MLflow tracking integration (env-gated) + metrics.json output"
git push origin feat/mlflow-tracking
gh pr create --title "Add MLflow tracking integration" --body "$(cat <<'EOF'
## Summary
- Add optional `mlflow` extra (mlflow-skinny)
- When `MLFLOW_TRACKING_URI` is set, `train/evaluate/predict` CLI commands auto-log params/metrics/artifacts to MLflow
- `evaluate` always writes `metrics.json` to `config.output.log` (platform-side consumers)
- Env-gated: no MLflow env → original behavior preserved
- Bumped to `0.5.0` (backward-compatible feature addition)

## Test plan
- [x] `uv run pytest` passes
- [x] `evaluate` without MLflow env produces metrics.json
- [x] `train` with MLflow env calls `mlflow.start_run(run_id=...)`
- [x] Non-numeric metrics excluded from `mlflow.log_metrics`
- [ ] upxelfdet bumps constraint to `islab-malware-detector[mlflow]>=0.5.0`
- [ ] lolday Phase 4 E2E uses new version

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Wait for PR to merge; tag + release `v0.5.0`:

```bash
git checkout main
git pull
git tag v0.5.0
git push origin v0.5.0
```

(If the project publishes to PyPI: `uv build && uv publish`. Otherwise detectors pull from Git tag.)

---

## Task 17: upxelfdet — Bump maldet Dependency

**Repo:** `upxelfdet`

- [ ] **Step 1: Clone + branch**

```bash
cd /home/bolin8017/Documents/repositories/upxelfdet
git fetch origin && git checkout main && git pull
git checkout -b feat/maldet-0.5.0-mlflow
```

- [ ] **Step 2: Update dependency**

Edit `pyproject.toml`:

```toml
dependencies = [
    "islab-malware-detector[mlflow]>=0.5.0",
    # ... existing ...
]
```

Bump version:

```toml
[project]
name = "upxelfdet"
version = "0.5.0"
```

- [ ] **Step 3: Verify tests still pass**

```bash
uv sync && uv run pytest 2>&1 | tail -20
```

Expected: existing upxelfdet tests pass. If any break due to MLflow default-import changes, gate via `MLFLOW_TRACKING_URI` unset in test env:

```python
# tests/conftest.py
import os
os.environ.pop("MLFLOW_TRACKING_URI", None)
```

- [ ] **Step 4: Commit + push + tag**

```bash
git add pyproject.toml
git commit -m "chore: bump islab-malware-detector to 0.5.0 (adds MLflow integration)"
git push origin feat/maldet-0.5.0-mlflow
gh pr create --title "Bump maldet to 0.5.0 (MLflow integration)" --body "Tracks the MLflow-enabled maldet. No code changes required here."
# After merge:
git checkout main && git pull && git tag v0.5.0 && git push origin v0.5.0
```

Note: lolday Phase 3 build pipeline rebuilds the detector image when a new tag is created. The lolday admin will rebuild upxelfdet:v0.5.0 via the detector UI or API after this task lands.

---

## Task 18: Deploy Scripts Update

**Files:**
- Modify: `scripts/deploy.sh`
- Create: `scripts/phase4-pre-deploy-check.sh`

- [ ] **Step 1: Write pre-deploy check script**

Create `scripts/phase4-pre-deploy-check.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Pre-flight checks for Phase 4 deploy.
# Confirms: sample dirs exist + readable, MLflow password set, Harbor is up.

MALWARE_DIR=${MALWARE_DIR:-/data/malware-samples}
BENIGN_DIR=${BENIGN_DIR:-/data/benign-samples}

echo "[1/4] Checking sample directories..."
for d in "$MALWARE_DIR" "$BENIGN_DIR"; do
  if [[ ! -d "$d" ]]; then
    echo "WARN: $d does not exist."
    echo "  → sudo mkdir -p $d && sudo chown $USER:$USER $d && sudo chmod 755 $d"
    echo "  (Phase 4 can deploy without samples, but jobs will fail integrity checks until populated.)"
  else
    echo "  OK: $d"
  fi
done

echo "[2/4] Checking MLflow DB password is set..."
if [[ -z "${MLFLOW_DB_PASSWORD:-}" ]]; then
  echo "FAIL: MLFLOW_DB_PASSWORD env var not set. Generate one:"
  echo "  export MLFLOW_DB_PASSWORD=\$(openssl rand -base64 32 | tr -d '=+/')"
  exit 1
fi
echo "  OK: MLFLOW_DB_PASSWORD present"

echo "[3/4] Checking Harbor is reachable from backend pod..."
if ! kubectl -n lolday exec deploy/backend -- curl -sf -o /dev/null http://harbor.harbor.svc:80/api/v2.0/health 2>/dev/null; then
  echo "WARN: Harbor health check failed from backend pod. Deploy may still work; investigate if Phase 3 pipeline was OK."
else
  echo "  OK: Harbor reachable"
fi

echo "[4/4] Checking PostgreSQL is reachable..."
if ! kubectl -n lolday exec statefulset/postgresql -- pg_isready -U postgres 2>/dev/null; then
  echo "FAIL: PostgreSQL not ready."
  exit 1
fi
echo "  OK: PostgreSQL ready"

echo
echo "All pre-deploy checks passed (or acknowledged warnings)."
```

`chmod +x scripts/phase4-pre-deploy-check.sh`.

- [ ] **Step 2: Extend deploy.sh**

Edit `scripts/deploy.sh`. After the Phase 3 Harbor install block, add:

```bash
echo "=== Phase 4: pre-deploy checks ==="
./scripts/phase4-pre-deploy-check.sh

echo "=== Phase 4: helm upgrade (adding MLflow + job NP) ==="
helm upgrade --install lolday ./charts/lolday \
  --namespace lolday --create-namespace \
  --set harbor.harborAdminPassword="${HARBOR_ADMIN_PASSWORD}" \
  --set backend.secrets.fernetKey="${FERNET_KEY}" \
  --set mlflow.db.password="${MLFLOW_DB_PASSWORD}"

echo "=== Phase 4: wait for MLflow ==="
kubectl -n lolday wait deploy/mlflow --for=condition=Available --timeout=180s

echo "=== Phase 4: smoke test MLflow from backend pod ==="
kubectl -n lolday exec deploy/backend -- curl -sf http://mlflow.lolday.svc:5000/health

echo
echo "Phase 4 deploy complete."
```

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy.sh scripts/phase4-pre-deploy-check.sh
git commit -m "feat(scripts): phase 4 pre-deploy checks + deploy flow extension"
```

---

## Task 19: Build + Push job-helper Image + Backend Image

**Files:** (no new files; operational task)

- [ ] **Step 1: Build job-helper image**

```bash
cd /home/bolin8017/Documents/repositories/lolday
# Harbor is accessed via service DNS resolved through the /etc/hosts entry
# pointing harbor.harbor.svc.cluster.local to the service ClusterIP (set up in Phase 3)
docker build -t harbor.harbor.svc.cluster.local:80/lolday/job-helper:v1 charts/lolday/helpers/job-helper/
```

- [ ] **Step 2: Login to Harbor and push**

```bash
# Use the robot account created in Phase 3 harbor init
docker login harbor.harbor.svc.cluster.local:80 \
  -u 'robot$build-pusher' \
  -p "$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | python3 -c 'import json,sys; print(json.load(sys.stdin)["auths"]["harbor.harbor.svc:80"]["password"])')"

docker push harbor.harbor.svc.cluster.local:80/lolday/job-helper:v1
```

Verify in Harbor UI or via API:

```bash
kubectl -n lolday exec deploy/backend -- curl -s http://harbor.harbor.svc:80/api/v2.0/projects/lolday/repositories/job-helper/artifacts | head -20
```

- [ ] **Step 3: Rebuild + push backend image**

The backend code changed significantly. Rebuild:

```bash
docker build -t harbor.harbor.svc.cluster.local:80/lolday/lolday-backend:latest backend/
docker push harbor.harbor.svc.cluster.local:80/lolday/lolday-backend:latest
kubectl -n lolday rollout restart deploy/backend
kubectl -n lolday rollout status deploy/backend --timeout=120s
```

- [ ] **Step 4: Verify Alembic migration applied**

```bash
kubectl -n lolday exec deploy/backend -- alembic current
```

Expected: latest revision ID (the Phase 4 migration's head). If the migration didn't run on startup (check Phase 3 Task — the Phase 2/3 backend should run `alembic upgrade head` in its startup). If not, run manually:

```bash
kubectl -n lolday exec deploy/backend -- alembic upgrade head
```

- [ ] **Step 5: Commit + push backend version marker (docs only)**

No git commit needed for this step (image build is a deploy-time action). If you want a lightweight tag:

```bash
cd /home/bolin8017/Documents/repositories/lolday
git tag phase4-deploy-$(date +%Y%m%d)
git push origin --tags
```

---

## Task 20: E2E Smoke Test

**Files:**
- Create: `docs/phase4-e2e-checklist.md`

- [ ] **Step 1: Write the checklist document**

Create `docs/phase4-e2e-checklist.md`:

```markdown
# Phase 4 E2E Smoke Test Checklist

**Purpose:** Validate end-to-end dataset + job + MLflow + Model Registry pipeline.

**Prerequisites:**
- Phase 3 deploy + E2E passed (upxelfdet successfully built to Harbor)
- Sample directories populated at `/data/malware-samples/` and `/data/benign-samples/`
  (at least ~10 malware + ~10 benign samples matching file_names in the test dataset)
- Phase 4 deploy completed (MLflow pod Running, backend Ready)
- upxelfdet v0.5.0 (or later MLflow-aware version) built and stored in Harbor:
  - Run: `curl -X POST /api/v1/detectors/<upxelfdet-id>/builds -d '{"git_tag": "v0.5.0"}'` and wait for SUCCEEDED
- Authenticated HTTP session (save JWT): `TOKEN=$(curl -s -X POST http://backend.lolday.svc:8000/api/v1/auth/login ... | jq -r .access_token)`

Port-forward to reach backend from dev machine:

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
```

---

## 1. Dataset Config CRUD

- [ ] Upload a small dataset config (subset of Malware202403_info.csv, ~100 rows matching samples on disk)

```bash
curl -X POST http://localhost:8000/api/v1/datasets \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"e2e-100\", \"csv_content\": \"$(cat /tmp/e2e-dataset.csv | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read())[1:-1])')\"}"
```

Expected: 201 with `sample_count=100`, `csv_checksum` non-empty.

- [ ] Upload a test split (50 rows)
- [ ] `GET /api/v1/datasets` returns both
- [ ] `GET /api/v1/datasets/{id}` returns metadata (no CSV content)
- [ ] `GET /api/v1/datasets/{id}/csv` returns raw CSV

## 2. Train Job

- [ ] Submit train job:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "train",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "train_dataset_id": "<train-ds-id>",
    "test_dataset_id": "<test-ds-id>",
    "params": {"seed": 42}
  }'
```

Expected: 202, returns `job_id`, `mlflow_run_id`, `status=preparing`.

- [ ] Poll status:

```bash
watch -n 2 "curl -s -H 'Authorization: Bearer $TOKEN' http://localhost:8000/api/v1/jobs/<job_id> | jq '.status, .failure_reason'"
```

Expected transitions: `preparing` → `running` → `succeeded` within ~10 min for 100 samples.

- [ ] Check K8s:

```bash
kubectl -n lolday get jobs -l lolday.job-type=train
kubectl -n lolday get pods -l lolday.job-type=train
kubectl -n lolday describe pod -l lolday.job-id=<job_id>
```

Expected: Pod transitioned `ContainerCreating` → `Running` → `Succeeded`; init containers finished 0.

- [ ] Check MLflow UI:

```bash
kubectl -n lolday port-forward svc/mlflow 5000:5000 &
# Open http://localhost:5000 in browser
```

Expected: experiment `detector:<upxelfdet-id>:v0.5.0` has 1 FINISHED run, with:
  - flat params (model.type, vectorize.method, etc.)
  - metrics (if autolog caught sklearn's SVM fit)
  - artifacts: `config.json`, `model/` with pickled model files

- [ ] Verify model_version row created:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/models | jq
```

Expected: one entry `{name: "upxelfdet", latest_version: 1}`.

## 3. Evaluate Job

- [ ] Submit evaluate:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "evaluate",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "test_dataset_id": "<test-ds-id>",
    "source_model_version_id": "<mv-id-from-step-2>",
    "params": {}
  }'
```

- [ ] Wait for `succeeded`.

- [ ] Check `GET /api/v1/jobs/{id}`:

Expected: `summary_metrics` populated with `{accuracy, precision, recall, f1, confusion_matrix?}`.

## 4. Predict Job

- [ ] Submit predict:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "predict",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "predict_dataset_id": "<predict-ds-id>",
    "source_model_version_id": "<mv-id>",
    "params": {}
  }'
```

- [ ] Wait for `succeeded`.

- [ ] Download prediction artifact via MLflow proxy:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/runs/<predict-run-id>/artifacts/download?path=prediction/prediction.csv" > /tmp/pred.csv
head /tmp/pred.csv
```

Expected: CSV with file_name + prediction columns.

## 5. Model Registry Transitions

- [ ] Promote v1 to Staging:

```bash
curl -X POST http://localhost:8000/api/v1/models/upxelfdet/versions/1/transition \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"to_stage": "Staging", "comment": "smoke test"}'
```

- [ ] Promote to Production:

```bash
curl -X POST http://localhost:8000/api/v1/models/upxelfdet/versions/1/transition \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"to_stage": "Production"}'
```

- [ ] Verify MLflow reflects the stage:

```bash
curl -s http://localhost:5000/api/2.0/mlflow/registered-models/get -d '{"name": "upxelfdet"}' | jq
```

Expected: `latest_versions[0].current_stage = "Production"`.

- [ ] Train a new version (v2), promote to Production; verify v1 auto-archives.

## 6. Error Paths

- [ ] Submit with bad params → expect 422:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"type": "train", "detector_version_id": "...", "train_dataset_id": "...", "params": {"seed": "not-an-int"}}'
```

- [ ] Submit duplicate within 5 min → expect 409.
- [ ] Exceed `JOB_PER_USER_CONCURRENCY` (2) → expect 429.
- [ ] Cancel running job:

```bash
curl -X POST http://localhost:8000/api/v1/jobs/<id>/cancel \
  -H "Authorization: Bearer $TOKEN"
```

Expected: Pod deleted within 15s; job row `cancelled`.

- [ ] Delete dataset with active job → expect 409.

## 7. NetworkPolicy Enforcement

- [ ] Shell into a running job pod (while it's active):

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s http://harbor.harbor.svc:80/ || echo BLOCKED'
```

Expected: `BLOCKED` (NetworkPolicy denies egress to Harbor).

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s http://mlflow.lolday.svc:5000/health'
```

Expected: `OK`.

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s https://github.com || echo BLOCKED'
```

Expected: `BLOCKED` (no internet egress).

## 8. SSH Safety Check

- [ ] After all the above, confirm SSH on port 9453 still responsive:

```bash
nc -zv server30 9453
```

Expected: `Connection to server30 9453 port [tcp/*] succeeded!`

- [ ] K3s still healthy:

```bash
ssh -p 9453 server30 'sudo systemctl is-active k3s'
```

Expected: `active`.

## Sign-off

- [ ] All dataset tests pass
- [ ] All 3 job types succeed
- [ ] Model Registry transitions work and archive on Production promotion
- [ ] Error paths return correct status codes
- [ ] NetworkPolicy blocks unintended egress
- [ ] SSH unaffected

On successful sign-off, Phase 4 is ready to squash-merge to `main`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/phase4-e2e-checklist.md
git commit -m "docs: phase 4 E2E smoke test checklist"
```

- [ ] **Step 3: Execute the checklist**

Actually perform every box above on server30. Record any failures + fixes as a separate commit:

```bash
git commit -m "fix: phase 4 E2E failures — <short description>"
```

- [ ] **Step 4: Merge dev → main**

After all checklist items signed off:

```bash
git checkout main
git merge --squash dev
git commit -m "$(cat <<'EOF'
feat: phase 4 — dataset & jobs (train/evaluate/predict + MLflow + Model Registry)

Delivers end-to-end detector execution:
- Dataset Config CRUD (inline CSV, SHA256 checksum, spot-check integrity)
- K8s Job-based train/evaluate/predict with MLflow tracking
- Model Registry (MLflow-backed) with Staging→Production→Archived transitions
- MLflow server + PV + shared PostgreSQL DB
- Sample PVs (hostPath) for malware + benign samples
- Strict deny-all NetworkPolicy on job pods (only DNS+MLflow+backend egress)
- upstream maldet v0.5.0 adds env-gated MLflow integration
- Job reconciler extends Phase 3 pattern with model registration + sync

Deployed + E2E-passed on server30.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Spec Coverage Summary

| Spec section                               | Implementing task(s) |
|--------------------------------------------|----------------------|
| §Dataset Storage (PVs, layout)             | Task 13              |
| §Dataset Config CRUD                       | Tasks 4, 5           |
| §Data Model                                | Task 2               |
| §API Endpoints: Datasets                   | Task 5               |
| §API Endpoints: Jobs                       | Task 9               |
| §API Endpoints: MLflow Proxy               | Task 10              |
| §API Endpoints: Model Registry             | Task 10              |
| §Job Submission & Lifecycle                | Task 9, Task 11      |
| §Job Pod Specification                     | Task 7               |
| §NetworkPolicy                             | Task 15              |
| §MLflow Deployment                         | Task 14              |
| §maldet Framework Changes (PR)             | Task 16              |
| §Job Helper Image                          | Task 12              |
| §Model Registry UX                         | Task 10              |
| §Security Summary                          | Tasks 7, 9, 15 (mechanisms); Task 20 (verification) |
| §Testing Strategy (unit)                   | Tasks 3, 4, 6, 7, 8, 9, 10, 11, 12 |
| §Testing Strategy (integration)            | Tasks 5, 9, 10       |
| §Testing Strategy (E2E)                    | Task 20              |
| §Helm / Deployment                         | Tasks 13, 14, 15, 18 |
| §Decisions & Amendments A8-A16             | (design-only, carried through Tasks 7, 9, 13, 14, 15) |
| §Open Questions                            | Task 20 (benign samples layout verified during E2E); Task 9 (detector CLI discovery via `detector.name`); Task 9 (test_dataset optional resolution) |

**Self-review notes:**
- `test_dataset` optionality: spec §Data Model marks it as nullable but type-matrix requires it for train. JobCreate schema requires it strictly; relax to optional only if a real detector needs it (deferred per spec Open Q3).
- Backend image is rebuilt in Task 19; Alembic migration applied on startup assumes lifespan runs `alembic upgrade head` (verify during Task 2; if Phase 3 doesn't, add it as a Task 2 sub-step).
- `dv.detector` lazy loading: requires SQLAlchemy relationship; documented in Task 9 Step 7 as a one-line addition. If Phase 3's `Detector` model lacks the back_populates, add it there without renaming.
- Tests under `test_experiments_proxy.py` need the `no_mock_mlflow` marker; registered in Task 10 Step 6.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-17-phase4-dataset-jobs.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration on early tasks while later tasks stay clean.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?

