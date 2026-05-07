# Model Registry — HuggingFace-style namespace + per-version visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the model-registry rebuild captured in `docs/superpowers/specs/2026-05-07-model-registry-namespace-and-visibility-design.md` — `User.handle` slug, `RegisteredModel` entity, per-version `visibility`, GitHub-style URL pattern `/api/v1/models/{owner}/{name}/...`, four new endpoints (description/tags, owner transfer, cascade delete, visibility toggle), reconciler change to namespaced MLflow names, predict-job visibility check, frontend route + UI refactor, and operator-driven elf-rf / elf-cnn rebuild as the end-to-end validation testbed.

**Architecture:** Two coordinated PRs that deploy together. **PR-A** (backend) ships schema + migration + endpoint rewrite + reconciler change in one atomic API contract change; tests run via aiosqlite. **PR-B** (frontend) regenerates `schema.gen.ts` from PR-A's OpenAPI, refactors the route file (`_authed.models.$name.tsx → $owner.$name.tsx`), adds 7 components + `react-markdown`, wires 6 TanStack Query mutations, and ships expanded vitest + Playwright coverage. Existing data is wiped pre-deploy (operator runs `wipe-mlflow.md` + DB DELETE), so the migration carries no backfill of model rows.

**Tech Stack:** FastAPI 0.115 + SQLAlchemy 2.0 async + Alembic + asyncpg/aiosqlite + Pydantic v2 + uv (backend); Vite 5 + React 18 + TypeScript 5.5 + react-router 7 + TanStack Query v5 + openapi-fetch + shadcn/ui + Tailwind 3.4 + react-i18next + react-markdown (new) + vitest + Playwright (frontend).

**Spec:** `docs/superpowers/specs/2026-05-07-model-registry-namespace-and-visibility-design.md`

**Working directories:**

- Backend: `cd backend/` — `uv run pytest`, `uv run ruff check .`, `uv run mypy`
- Frontend: `cd frontend/` — `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm format:check`

---

# Phase A — PR-A: Backend

Open PR-A as branch `feat/model-registry-namespace-pr-a-backend` once Phase A completes. Migration runs against fresh schema (operator wipes pre-deploy per Phase C Task 37).

## Task 1: User.handle slug derivation utility

Pure function used by migration backfill (Task 4) and cf_access auto-assignment (Task 5). TDD-first because it has no DB dependency.

**Files:**

- Create: `backend/app/services/user_handle.py`
- Create: `backend/tests/test_user_handle.py`

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/test_user_handle.py`:

```python
"""Unit tests for User.handle slug derivation."""

import pytest

from app.services.user_handle import (
    HANDLE_MAX_LEN,
    derive_handle_from_email,
    is_valid_handle,
    next_unique_handle,
)


class TestIsValidHandle:
    @pytest.mark.parametrize("h", ["bolin8017", "alice", "elf-rf", "user_42", "u-1"])
    def test_valid(self, h):
        assert is_valid_handle(h) is True

    @pytest.mark.parametrize(
        "h",
        [
            "",                # empty
            "1abc",            # starts with digit
            "-abc",            # starts with hyphen
            "abc-",            # ends with hyphen
            "ab--cd",          # consecutive hyphens
            "ABC",             # uppercase
            "user@x",          # invalid char
            "a" * 61,          # too long
        ],
    )
    def test_invalid(self, h):
        assert is_valid_handle(h) is False


class TestDeriveHandleFromEmail:
    def test_simple_prefix(self):
        assert derive_handle_from_email("bolin8017@gmail.com") == "bolin8017"

    def test_dot_in_prefix_replaced_with_hyphen(self):
        assert derive_handle_from_email("first.last@x.com") == "first-last"

    def test_underscore_preserved(self):
        assert derive_handle_from_email("first_last@x.com") == "first_last"

    def test_uppercase_lowered(self):
        assert derive_handle_from_email("Alice@X.com") == "alice"

    def test_starts_with_digit_prepends_u(self):
        assert derive_handle_from_email("123abc@x.com") == "u-123abc"

    def test_invalid_chars_replaced_with_hyphen(self):
        assert derive_handle_from_email("a+b!c@x.com") == "a-b-c"

    def test_collapses_double_hyphens(self):
        assert derive_handle_from_email("a..b@x.com") == "a-b"

    def test_strips_leading_trailing_hyphens(self):
        assert derive_handle_from_email("-foo-@x.com") == "foo"

    def test_truncated_to_max_len(self):
        long_email = "a" * 80 + "@x.com"
        result = derive_handle_from_email(long_email)
        assert len(result) <= HANDLE_MAX_LEN
        assert is_valid_handle(result)

    def test_empty_local_part_falls_back(self):
        # Synthetic CF-Access service-token edge case
        result = derive_handle_from_email("@cf-access.local")
        assert is_valid_handle(result)
        assert result.startswith("u-")


class TestNextUniqueHandle:
    def test_returns_base_when_unused(self):
        assert next_unique_handle("alice", existing=set()) == "alice"

    def test_appends_suffix_2_on_collision(self):
        assert next_unique_handle("alice", existing={"alice"}) == "alice-2"

    def test_increments_until_unique(self):
        existing = {"alice", "alice-2", "alice-3"}
        assert next_unique_handle("alice", existing=existing) == "alice-4"

    def test_truncates_base_to_make_room_for_suffix(self):
        long_base = "a" * HANDLE_MAX_LEN
        existing = {long_base}
        result = next_unique_handle(long_base, existing=existing)
        assert len(result) <= HANDLE_MAX_LEN
        assert result.endswith("-2")
        assert is_valid_handle(result)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend && uv run pytest tests/test_user_handle.py -v
```

Expected: ImportError or `ModuleNotFoundError: No module named 'app.services.user_handle'`.

- [ ] **Step 3: Implement the utility**

Create `backend/app/services/user_handle.py`:

```python
"""Slug derivation rules for `User.handle`.

Mirrors HuggingFace / GitHub conventions: lowercase alphanumeric +
`_` + `-`, must start with a letter, no trailing `-`, no consecutive
`--`, length 1..60. The migration (one-shot) and cf_access (per-login)
both call ``derive_handle_from_email`` and resolve collisions via
``next_unique_handle``.
"""

from __future__ import annotations

import re
import uuid

HANDLE_MAX_LEN = 60
_VALID_RE = re.compile(r"^[a-z][a-z0-9_-]*[a-z0-9]$|^[a-z]$")


def is_valid_handle(handle: str) -> bool:
    if not handle or len(handle) > HANDLE_MAX_LEN:
        return False
    if "--" in handle:
        return False
    return bool(_VALID_RE.fullmatch(handle))


def _slugify(raw: str) -> str:
    s = raw.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s


def derive_handle_from_email(email: str) -> str:
    """Slug-safe derivation; resolution to uniqueness is caller's job."""
    local = email.split("@", 1)[0]
    handle = _slugify(local)

    if not handle:
        # CF-Access service token / weird email: fall back to UUID short form
        handle = "u-" + uuid.uuid4().hex[:8]
    elif handle[0].isdigit():
        handle = "u-" + handle

    if len(handle) > HANDLE_MAX_LEN:
        handle = handle[:HANDLE_MAX_LEN].rstrip("-")

    return handle


def next_unique_handle(base: str, *, existing: set[str]) -> str:
    """Return ``base`` if unused, else ``base-2``, ``base-3``, ... — first unused."""
    if base not in existing:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        room = HANDLE_MAX_LEN - len(suffix)
        candidate = base[:room].rstrip("-") + suffix
        if candidate not in existing:
            return candidate
        n += 1
```

- [ ] **Step 4: Run tests, all green**

```bash
cd backend && uv run pytest tests/test_user_handle.py -v
```

Expected: 21 passed (or whatever total).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/user_handle.py backend/tests/test_user_handle.py
git commit -m "feat(backend): add User.handle slug derivation utility"
```

---

## Task 2: SQLAlchemy models — User.handle + RegisteredModel + ModelVersion refactor + audit logs

Models are non-test code; alembic autogenerate (Task 4) reads from them. Tests come later via endpoint integration.

**Files:**

- Modify: `backend/app/models/user.py`
- Modify: `backend/app/models/model_registry.py`
- Modify: `backend/app/models/__init__.py` (re-exports)

- [ ] **Step 1: Add `handle` to `User`**

Edit `backend/app/models/user.py`. Insert after the `email` field:

```python
    handle: Mapped[str] = mapped_column(
        String(60),
        unique=True,
        nullable=False,
        index=True,
    )
```

- [ ] **Step 2: Replace `backend/app/models/model_registry.py`**

Full new content:

```python
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, User

# JSONB on PostgreSQL, plain JSON on SQLite (test).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class ModelVersionStage(StrEnum):
    NONE = "None"
    STAGING = "Staging"
    PRODUCTION = "Production"
    ARCHIVED = "Archived"


class ModelVersionVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class RegisteredModel(Base):
    __tablename__ = "registered_model"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[dict] = mapped_column(_JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    detector: Mapped["Detector"] = relationship()  # type: ignore[name-defined]

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "detector_id", name="uq_registered_model_owner_detector"
        ),
        Index("ix_registered_model_owner", "owner_id"),
    )

    @property
    def mlflow_name(self) -> str:
        """`{handle}/{detector.name}` — derived, never stored."""
        return f"{self.owner.handle}/{self.detector.name}"


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    mlflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    current_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelVersionStage.NONE,
        nullable=False,
    )
    visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelVersionVisibility.PRIVATE,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    source_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    registered_model: Mapped[RegisteredModel] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "registered_model_id",
            "mlflow_version",
            name="uq_model_version_per_registered",
        ),
        Index("ix_model_version_registered_model", "registered_model_id"),
        Index("ix_model_version_owner", "owner_id"),
        Index("ix_model_version_stage", "current_stage"),
        Index("ix_model_version_visibility", "visibility"),
    )


class ModelTransitionLog(Base):
    """Existing audit table — schema unchanged."""

    __tablename__ = "model_transition_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False
    )
    from_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    to_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_model_transition_version", "model_version_id"),)


class ModelVisibilityLog(Base):
    __tablename__ = "model_visibility_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False
    )
    from_visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    to_visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_model_visibility_log_version", "model_version_id"),)


class ModelOwnerTransferLog(Base):
    __tablename__ = "model_owner_transfer_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    from_owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    to_owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_model_owner_transfer_log_model", "registered_model_id"),
    )
```

- [ ] **Step 3: Update `backend/app/models/__init__.py`**

Re-export the new symbols. Find the existing re-exports for `model_registry` and replace with:

```python
from app.models.model_registry import (
    ModelOwnerTransferLog,
    ModelTransitionLog,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    ModelVisibilityLog,
    RegisteredModel,
)
```

- [ ] **Step 4: Run typecheck — models compile**

```bash
cd backend && uv run mypy app/models/
```

Expected: no errors (or only pre-existing untouched-module errors).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/
git commit -m "feat(backend): refactor model_registry — add RegisteredModel, drop mlflow_name on ModelVersion, add visibility column + audit logs"
```

---

## Task 3: Pydantic schemas update

`backend/app/schemas/model_registry.py` — replace whole file. Used by routers in Tasks 8–14.

**Files:**

- Modify (rewrite): `backend/app/schemas/model_registry.py`

- [ ] **Step 1: Replace file content**

```python
"""Pydantic schemas for the model registry layer."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.model_registry import ModelVersionStage, ModelVersionVisibility


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mlflow_version: int
    mlflow_run_id: str
    current_stage: ModelVersionStage
    visibility: ModelVersionVisibility
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
    """One row in `GET /api/v1/models`."""

    owner: str  # user.handle
    name: str  # detector.name
    description: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    latest_version: int | None = None
    latest_production_version: int | None = None
    latest_staging_version: int | None = None


class RegisteredModelRead(BaseModel):
    """Full detail for `GET /api/v1/models/{owner}/{name}`."""

    model_config = ConfigDict(from_attributes=True)

    owner: str
    name: str
    description: str | None
    tags: dict[str, str]
    latest_version: int | None
    latest_production_version: int | None
    latest_staging_version: int | None
    created_at: datetime


class RegisteredModelUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    tags: dict[str, str] | None = None


class OwnerTransferRequest(BaseModel):
    new_owner_handle: str = Field(min_length=1, max_length=60)
    comment: str | None = Field(default=None, max_length=1000)


class ModelTransitionRequest(BaseModel):
    """Stage transition — schema unchanged from existing."""

    to_stage: ModelVersionStage
    comment: str | None = Field(default=None, max_length=1000)


class ModelVersionVisibilityUpdate(BaseModel):
    visibility: ModelVersionVisibility
    comment: str | None = Field(default=None, max_length=1000)
```

- [ ] **Step 2: Run typecheck**

```bash
cd backend && uv run mypy app/schemas/
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/model_registry.py
git commit -m "feat(backend): rewrite model_registry Pydantic schemas for namespaced URL pattern"
```

---

## Task 4: Alembic migration

Single revision: adds `user.handle`, creates `registered_model` + `model_visibility_log` + `model_owner_transfer_log`, refactors `model_version` (drop `mlflow_name`, add `registered_model_id` FK + `visibility`).

**Pre-condition assumption:** `model_version` and `model_transition_log` tables are empty when this migration runs (operator wipes pre-deploy per Phase C Task 37). The migration will fail loudly if rows exist, by design.

**Files:**

- Create: `backend/migrations/versions/<auto>_phase_model_namespace.py`

- [ ] **Step 1: Generate revision skeleton**

```bash
cd backend && uv run alembic revision -m "phase model namespace and visibility"
```

Expected: prints path like `migrations/versions/abc123def456_phase_model_namespace_and_visibility.py`. Open and fully replace the body.

- [ ] **Step 2: Replace upgrade() + downgrade()**

```python
"""phase model namespace and visibility

Revision ID: <auto>
Revises: <previous head>
Create Date: 2026-05-07 ...
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "<auto>"           # filled by generator
down_revision = "<prev_head>" # filled by generator
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. user.handle ----
    op.add_column(
        "user",
        sa.Column("handle", sa.String(60), nullable=True),
    )

    # Backfill: derive + collision-resolve handle for every existing user.
    from app.services.user_handle import (
        derive_handle_from_email,
        next_unique_handle,
    )

    rows = bind.execute(
        sa.text('SELECT id, email FROM "user" ORDER BY created_at')
    ).all()
    used: set[str] = set()
    for row in rows:
        base = derive_handle_from_email(row.email)
        handle = next_unique_handle(base, existing=used)
        used.add(handle)
        bind.execute(
            sa.text('UPDATE "user" SET handle = :h WHERE id = :id'),
            {"h": handle, "id": row.id},
        )

    op.alter_column("user", "handle", nullable=False)
    op.create_index("ix_user_handle", "user", ["handle"], unique=True)

    # ---- 2. New enum type ----
    visibility_enum = sa.Enum(
        "public", "private", name="model_version_visibility_enum"
    )
    visibility_enum.create(bind, checkfirst=False)

    # ---- 3. registered_model table ----
    op.create_table(
        "registered_model",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "detector_id",
            sa.Uuid(),
            sa.ForeignKey("detector.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'::jsonb")
            if bind.dialect.name == "postgresql"
            else sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "owner_id", "detector_id", name="uq_registered_model_owner_detector"
        ),
    )
    op.create_index(
        "ix_registered_model_owner", "registered_model", ["owner_id"]
    )

    # ---- 4. Refactor model_version ----
    # Pre-condition: table is empty.
    row_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM model_version")
    ).scalar()
    if row_count and row_count > 0:
        raise RuntimeError(
            f"model_version has {row_count} rows; this migration requires "
            "an empty table. Run pre-deploy wipe (see spec §4.3)."
        )

    # Drop existing unique index (will be replaced).
    op.drop_index(
        "ix_model_version_name_version_unique", table_name="model_version"
    )

    op.drop_column("model_version", "mlflow_name")
    op.add_column(
        "model_version",
        sa.Column(
            "registered_model_id",
            sa.Uuid(),
            sa.ForeignKey("registered_model.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.add_column(
        "model_version",
        sa.Column(
            "visibility",
            sa.Enum(
                "public", "private", name="model_version_visibility_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="private",
        ),
    )
    op.alter_column("model_version", "visibility", server_default=None)
    op.create_unique_constraint(
        "uq_model_version_per_registered",
        "model_version",
        ["registered_model_id", "mlflow_version"],
    )
    op.create_index(
        "ix_model_version_registered_model",
        "model_version",
        ["registered_model_id"],
    )
    op.create_index(
        "ix_model_version_visibility", "model_version", ["visibility"]
    )

    # ---- 5. Audit log tables ----
    op.create_table(
        "model_visibility_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "model_version_id",
            sa.Uuid(),
            sa.ForeignKey("model_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_visibility",
            sa.Enum(
                "public", "private", name="model_version_visibility_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "to_visibility",
            sa.Enum(
                "public", "private", name="model_version_visibility_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_model_visibility_log_version",
        "model_visibility_log",
        ["model_version_id"],
    )

    op.create_table(
        "model_owner_transfer_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "registered_model_id",
            sa.Uuid(),
            sa.ForeignKey("registered_model.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column(
            "to_owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "transferred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_model_owner_transfer_log_model",
        "model_owner_transfer_log",
        ["registered_model_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_owner_transfer_log_model", table_name="model_owner_transfer_log"
    )
    op.drop_table("model_owner_transfer_log")

    op.drop_index(
        "ix_model_visibility_log_version", table_name="model_visibility_log"
    )
    op.drop_table("model_visibility_log")

    op.drop_index("ix_model_version_visibility", table_name="model_version")
    op.drop_index(
        "ix_model_version_registered_model", table_name="model_version"
    )
    op.drop_constraint(
        "uq_model_version_per_registered", "model_version", type_="unique"
    )
    op.drop_column("model_version", "visibility")
    op.drop_column("model_version", "registered_model_id")
    op.add_column(
        "model_version",
        sa.Column("mlflow_name", sa.String(200), nullable=False),
    )
    op.create_index(
        "ix_model_version_name_version_unique",
        "model_version",
        ["mlflow_name", "mlflow_version"],
        unique=True,
    )

    op.drop_index("ix_registered_model_owner", table_name="registered_model")
    op.drop_table("registered_model")

    sa.Enum(name="model_version_visibility_enum").drop(
        op.get_bind(), checkfirst=False
    )

    op.drop_index("ix_user_handle", table_name="user")
    op.drop_column("user", "handle")
```

- [ ] **Step 3: Run upgrade against fresh sqlite (test conftest)**

```bash
cd backend && uv run pytest tests/conftest.py --co -q
```

This triggers conftest's auto-migrate path (it runs `alembic upgrade head` against in-memory aiosqlite). Expected: silent success (collection succeeds — no errors).

- [ ] **Step 4: Manual sanity — verify schema**

```bash
cd backend && uv run python -c "
import asyncio
from app.db import get_engine
from sqlalchemy import inspect

async def check():
    eng = get_engine()
    async with eng.connect() as conn:
        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            print('tables:', sorted(insp.get_table_names()))
            print('user cols:', [c['name'] for c in insp.get_columns('user')])
            print('model_version cols:', [c['name'] for c in insp.get_columns('model_version')])
        await conn.run_sync(_inspect)

asyncio.run(check())
"
```

Expected output includes `registered_model`, `model_visibility_log`, `model_owner_transfer_log` in tables; `handle` in user cols; `registered_model_id` and `visibility` in model_version cols; **no** `mlflow_name`.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/
git commit -m "feat(backend): alembic migration for namespace + visibility schema"
```

---

## Task 5: cf_access — auto-assign handle on first login

Lifecycle: when CF Access JWT validates and we land a new `User` row, derive + uniqueness-check the handle. Reuse the same utility migration uses.

**Files:**

- Modify: `backend/app/auth/cf_access.py` (the function that creates/upserts User rows)

- [ ] **Step 1: Locate the user upsert path**

```bash
cd backend && grep -n "User(" app/auth/cf_access.py
```

Note the line where a new `User(...)` is constructed.

- [ ] **Step 2: Inject handle assignment**

In the user-creation branch, after determining `email`, derive + collision-resolve. Add at top:

```python
from sqlalchemy import select

from app.services.user_handle import (
    derive_handle_from_email,
    next_unique_handle,
)
```

Replace the `User(...)` instantiation block with:

```python
base = derive_handle_from_email(email)
existing = set(
    (
        await session.execute(select(User.handle))
    ).scalars().all()
)
handle = next_unique_handle(base, existing=existing)
user = User(email=email, role=role, handle=handle)
session.add(user)
```

(Adjust to match the actual variable names already in the file — e.g. if role determination is conditional.)

- [ ] **Step 3: Add a regression test**

Append to `backend/tests/test_auth_cf_access.py` (file exists; append a new test method):

```python
async def test_first_login_derives_handle(async_client, mock_cf_jwt):
    mock_cf_jwt({"email": "newuser@example.com", "sub": "abc"})
    resp = await async_client.get("/api/v1/users/me")
    assert resp.status_code == 200
    assert resp.json()["handle"] == "newuser"


async def test_handle_collision_appends_suffix(async_client, mock_cf_jwt, session):
    # Pre-create a user occupying "alice"
    from app.models import User
    session.add(User(email="alice@first.com", role="developer", handle="alice"))
    await session.commit()

    mock_cf_jwt({"email": "alice@second.com", "sub": "xyz"})
    resp = await async_client.get("/api/v1/users/me")
    assert resp.json()["handle"] == "alice-2"
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_auth_cf_access.py -v
```

Expected: pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/cf_access.py backend/tests/test_auth_cf_access.py
git commit -m "feat(backend): auto-derive User.handle on first cf-access login"
```

---

## Task 6: MLflow client extensions

Add `rename_registered_model` and `delete_registered_model` (and confirm `delete_model_version` exists). Required by Tasks 13 (transfer) and 14 (delete).

**Files:**

- Modify: `backend/app/services/mlflow_client.py`

- [ ] **Step 1: Inspect existing methods**

```bash
cd backend && grep -n "async def " app/services/mlflow_client.py
```

Confirm: `create_registered_model`, `create_model_version`, `transition_model_version_stage`, `delete_model_version`, `search_*` exist. Confirm `rename_registered_model` and `delete_registered_model` are absent.

- [ ] **Step 2: Add the missing methods**

Append inside the class (before `__all__` if present):

```python
    async def rename_registered_model(
        self, name: str, new_name: str
    ) -> dict[str, Any]:
        resp = await self._post_json(
            "/api/2.0/mlflow/registered-models/rename",
            {"name": name, "new_name": new_name},
        )
        return resp["registered_model"]

    async def delete_registered_model(self, name: str) -> None:
        await self._post_json(
            "/api/2.0/mlflow/registered-models/delete",
            {"name": name},
        )
```

(Method signatures match MLflow REST API 2.0; reuse the existing `_post_json` private helper.)

- [ ] **Step 3: Add unit test against the autouse mock**

Append to `backend/tests/test_mlflow_client.py` (or create if absent):

```python
async def test_rename_registered_model(mock_mlflow):
    from app.services.mlflow_client import MlflowClient

    mock_mlflow.set_response(
        "/api/2.0/mlflow/registered-models/rename",
        {"registered_model": {"name": "alice/elf-rf"}},
    )
    client = MlflowClient(base_url="http://mlflow")
    result = await client.rename_registered_model("bolin8017/elf-rf", "alice/elf-rf")
    assert result["name"] == "alice/elf-rf"


async def test_delete_registered_model(mock_mlflow):
    from app.services.mlflow_client import MlflowClient

    client = MlflowClient(base_url="http://mlflow")
    await client.delete_registered_model("bolin8017/elf-rf")
    assert mock_mlflow.last_call().path.endswith("/registered-models/delete")
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_mlflow_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mlflow_client.py backend/tests/test_mlflow_client.py
git commit -m "feat(backend): add rename/delete registered_model on MlflowClient"
```

---

## Task 7: `_resolve_registered_model` helper

Service-layer access control central; reused by every endpoint in Tasks 8–14.

**Files:**

- Modify: `backend/app/services/model_registry.py` (existing file — append helper)

- [ ] **Step 1: Append helper**

```python
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Detector,
    ModelVersion,
    ModelVersionVisibility,
    RegisteredModel,
    User,
)


async def resolve_registered_model(
    owner: str,
    name: str,
    session: AsyncSession,
    user: User,
    *,
    write: bool = False,
) -> RegisteredModel:
    """Centralised access control for `/models/{owner}/{name}/...` endpoints.

    Read path: returns 404 if the model doesn't exist OR if every version is
    private and the caller isn't owner/admin (hide-existence pattern, mirrors
    `datasets._get_readable_dataset`).

    Write path: returns 403 if caller isn't owner/admin (mirrors
    `_get_writable_dataset`).
    """
    rm = (
        await session.execute(
            select(RegisteredModel)
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .where(User.handle == owner, Detector.name == name)
        )
    ).scalar_one_or_none()
    if rm is None:
        raise HTTPException(404, "model not found")

    is_owner = rm.owner_id == user.id
    is_admin = user.role.value == "admin"

    if write and not (is_owner or is_admin):
        raise HTTPException(403, "owner or admin only")

    if not write and not (is_owner or is_admin):
        any_visible = (
            await session.execute(
                select(func.count())
                .select_from(ModelVersion)
                .where(
                    ModelVersion.registered_model_id == rm.id,
                    ModelVersion.visibility == ModelVersionVisibility.PUBLIC,
                )
            )
        ).scalar()
        if not any_visible:
            raise HTTPException(404, "model not found")

    return rm
```

- [ ] **Step 2: Add tests for the helper**

Create `backend/tests/test_model_registry_resolver.py`:

```python
"""Unit tests for `resolve_registered_model` access control helper."""

import pytest
from fastapi import HTTPException

from app.models import (
    Detector,
    ModelVersion,
    ModelVersionVisibility,
    RegisteredModel,
    Role,
    User,
)
from app.services.model_registry import resolve_registered_model


@pytest.fixture
async def two_users_and_detector(session):
    alice = User(email="alice@x.com", handle="alice", role=Role.DEVELOPER)
    bob = User(email="bob@x.com", handle="bob", role=Role.DEVELOPER)
    admin = User(email="adm@x.com", handle="admin", role=Role.ADMIN)
    session.add_all([alice, bob, admin])
    await session.flush()
    det = Detector(
        name="elf-rf", display_name="ELF RF",
        git_url="https://github.com/x/y", owner_id=alice.id,
    )
    session.add(det)
    await session.flush()
    return alice, bob, admin, det


async def _make_rm_with_version(session, *, owner, detector, visibility):
    rm = RegisteredModel(owner_id=owner.id, detector_id=detector.id)
    session.add(rm)
    await session.flush()
    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=1,
        mlflow_run_id="r-1",
        visibility=visibility,
        detector_version_id=...,  # add fixture if needed
        source_job_id=...,
        owner_id=owner.id,
    )
    session.add(mv)
    await session.flush()
    return rm


async def test_owner_read_succeeds(session, two_users_and_detector):
    alice, _, _, det = two_users_and_detector
    rm = await _make_rm_with_version(
        session, owner=alice, detector=det, visibility=ModelVersionVisibility.PRIVATE
    )
    out = await resolve_registered_model("alice", "elf-rf", session, alice)
    assert out.id == rm.id


async def test_non_owner_404_on_all_private(session, two_users_and_detector):
    alice, bob, _, det = two_users_and_detector
    await _make_rm_with_version(
        session, owner=alice, detector=det, visibility=ModelVersionVisibility.PRIVATE
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model("alice", "elf-rf", session, bob)
    assert exc.value.status_code == 404


async def test_non_owner_200_when_any_public(session, two_users_and_detector):
    alice, bob, _, det = two_users_and_detector
    await _make_rm_with_version(
        session, owner=alice, detector=det, visibility=ModelVersionVisibility.PUBLIC
    )
    out = await resolve_registered_model("alice", "elf-rf", session, bob)
    assert out is not None


async def test_admin_sees_private(session, two_users_and_detector):
    alice, _, admin, det = two_users_and_detector
    await _make_rm_with_version(
        session, owner=alice, detector=det, visibility=ModelVersionVisibility.PRIVATE
    )
    out = await resolve_registered_model("alice", "elf-rf", session, admin)
    assert out is not None


async def test_write_non_owner_403(session, two_users_and_detector):
    alice, bob, _, det = two_users_and_detector
    await _make_rm_with_version(
        session, owner=alice, detector=det, visibility=ModelVersionVisibility.PUBLIC
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model(
            "alice", "elf-rf", session, bob, write=True
        )
    assert exc.value.status_code == 403


async def test_404_when_model_not_found(session, two_users_and_detector):
    alice, _, _, _ = two_users_and_detector
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model(
            "alice", "nonexistent", session, alice
        )
    assert exc.value.status_code == 404
```

- [ ] **Step 3: Run tests**

```bash
cd backend && uv run pytest tests/test_model_registry_resolver.py -v
```

Expected: 6 pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/model_registry.py backend/tests/test_model_registry_resolver.py
git commit -m "feat(backend): add resolve_registered_model access-control helper"
```

---

## Task 8: List endpoint — `GET /api/v1/models`

Single SQL with conditional aggregation (CASE inside MAX) — solves visibility filtering and the existing N+1 in one rewrite.

**Files:**

- Modify (rewrite): `backend/app/routers/models_registry.py`
- Create: `backend/tests/test_models_list.py`

- [ ] **Step 1: Write tests first**

`backend/tests/test_models_list.py`:

```python
"""Tests for GET /api/v1/models."""

import pytest

from app.models import ModelVersionStage, ModelVersionVisibility


@pytest.fixture
async def populated(session, make_user, make_detector, make_registered_model_with_versions):
    """Two users, two detectors, mixed visibility versions."""
    alice = await make_user(handle="alice", role="developer")
    bob = await make_user(handle="bob", role="developer")
    det_rf = await make_detector(name="elf-rf", owner=alice)
    det_cnn = await make_detector(name="elf-cnn", owner=alice)

    # alice/elf-rf: 1 public + 1 private
    await make_registered_model_with_versions(
        owner=alice, detector=det_rf,
        versions=[
            (1, ModelVersionVisibility.PUBLIC, ModelVersionStage.PRODUCTION),
            (2, ModelVersionVisibility.PRIVATE, ModelVersionStage.STAGING),
        ],
    )
    # bob/elf-rf: only private
    await make_registered_model_with_versions(
        owner=bob, detector=det_rf,
        versions=[
            (1, ModelVersionVisibility.PRIVATE, ModelVersionStage.NONE),
        ],
    )
    # alice/elf-cnn: only public
    await make_registered_model_with_versions(
        owner=alice, detector=det_cnn,
        versions=[(1, ModelVersionVisibility.PUBLIC, ModelVersionStage.PRODUCTION)],
    )
    return alice, bob


async def test_alice_sees_own_private_plus_public(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models")
    assert resp.status_code == 200
    rows = resp.json()
    names = sorted(f"{r['owner']}/{r['name']}" for r in rows)
    # alice sees: alice/elf-rf (full), alice/elf-cnn (full); bob/elf-rf NOT
    assert names == ["alice/elf-cnn", "alice/elf-rf"]


async def test_bob_sees_alices_public_and_own_private(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models")
    rows = {f"{r['owner']}/{r['name']}": r for r in resp.json()}
    assert "alice/elf-rf" in rows  # has public v1
    assert "alice/elf-cnn" in rows  # all public
    assert "bob/elf-rf" in rows  # own private


async def test_alice_summary_counts_only_visible(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models")
    row = next(r for r in resp.json() if r["name"] == "elf-rf" and r["owner"] == "alice")
    # alice sees both v1 (public) and v2 (private, owned)
    assert row["latest_version"] == 2
    assert row["latest_production_version"] == 1
    assert row["latest_staging_version"] == 2


async def test_bob_summary_counts_only_visible(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models")
    row = next(r for r in resp.json() if r["name"] == "elf-rf" and r["owner"] == "alice")
    # bob sees only v1 (public) of alice/elf-rf
    assert row["latest_version"] == 1
    assert row["latest_production_version"] == 1
    assert row["latest_staging_version"] is None


async def test_filter_owner(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models?owner=bob")
    assert resp.json() == []  # alice can't see bob's all-private models


async def test_filter_visibility_mine(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models?visibility=mine")
    rows = [f"{r['owner']}/{r['name']}" for r in resp.json()]
    assert sorted(rows) == ["alice/elf-cnn", "alice/elf-rf"]


async def test_filter_visibility_public(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models?visibility=public")
    rows = [f"{r['owner']}/{r['name']}" for r in resp.json()]
    assert "alice/elf-cnn" in rows  # has public version
    assert "bob/elf-rf" not in rows  # only private


async def test_admin_sees_all(populated, client_as, make_user):
    admin = await make_user(handle="admin", role="admin")
    resp = await client_as(admin).get("/api/v1/models")
    rows = sorted(f"{r['owner']}/{r['name']}" for r in resp.json())
    assert rows == ["alice/elf-cnn", "alice/elf-rf", "bob/elf-rf"]
```

(`make_user`, `make_detector`, `make_registered_model_with_versions`, `client_as` are conftest fixtures — add them to `backend/tests/conftest.py` if absent. They wrap session ops + auth-mock client. See conftest at end of Task 18 for full fixture.)

- [ ] **Step 2: Run tests, expect failure**

```bash
cd backend && uv run pytest tests/test_models_list.py -v
```

Expected: 8 fail (route doesn't yet exist or returns wrong shape).

- [ ] **Step 3: Implement the endpoint**

Replace top of `backend/app/routers/models_registry.py` (keep the file but rewrite progressively). For Task 8 specifically, the list endpoint:

```python
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import current_active_user
from app.models import (
    Detector,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    RegisteredModel,
    Role,
    User,
)
from app.schemas.model_registry import RegisteredModelSummary

router = APIRouter()


@router.get("", response_model=list[RegisteredModelSummary])
async def list_models(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    owner: str | None = Query(default=None),
    visibility: Literal["all", "public", "mine"] = Query(default="all"),
) -> list[RegisteredModelSummary]:
    visible = (
        ModelVersion.visibility == ModelVersionVisibility.PUBLIC
    ) | (ModelVersion.owner_id == user.id)
    if user.role == Role.ADMIN:
        visible = sa.true()

    stmt = (
        select(
            User.handle.label("owner"),
            Detector.name.label("name"),
            RegisteredModel.description,
            RegisteredModel.tags,
            func.max(ModelVersion.mlflow_version).label("latest_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_production_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.STAGING,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_staging_version"),
        )
        .select_from(RegisteredModel)
        .join(User, RegisteredModel.owner_id == User.id)
        .join(Detector, RegisteredModel.detector_id == Detector.id)
        .join(ModelVersion, ModelVersion.registered_model_id == RegisteredModel.id)
        .where(visible)
        .group_by(RegisteredModel.id, User.handle, Detector.name)
    )

    if owner is not None:
        stmt = stmt.where(User.handle == owner)
    if visibility == "public":
        stmt = stmt.having(
            func.count(case(
                (ModelVersion.visibility == ModelVersionVisibility.PUBLIC, 1),
                else_=None
            )) > 0
        )
    elif visibility == "mine":
        stmt = stmt.where(RegisteredModel.owner_id == user.id)

    rows = (await session.execute(stmt)).all()
    return [RegisteredModelSummary(**r._mapping) for r in rows]
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd backend && uv run pytest tests/test_models_list.py -v
```

Expected: 8 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_list.py
git commit -m "feat(backend): rewrite GET /api/v1/models with namespace + visibility filter"
```

---

## Task 9: Get summary + version endpoints

`GET /api/v1/models/{owner}/{name}` and `GET /api/v1/models/{owner}/{name}/versions[/{version}]`. Mirror Task 8 patterns; reuse `resolve_registered_model` for access.

**Files:**

- Modify: `backend/app/routers/models_registry.py` (append)
- Create: `backend/tests/test_models_get.py`

- [ ] **Step 1: Tests**

```python
# backend/tests/test_models_get.py
async def test_get_summary_owner(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models/alice/elf-rf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == "alice" and body["name"] == "elf-rf"
    assert body["latest_version"] == 2  # owner sees private


async def test_get_summary_non_owner_only_public(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models/alice/elf-rf")
    body = resp.json()
    assert body["latest_version"] == 1


async def test_get_summary_404_for_all_private(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models/bob/elf-rf")
    assert resp.status_code == 404


async def test_list_versions_filters_private(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models/alice/elf-rf/versions")
    versions = [v["mlflow_version"] for v in resp.json()["items"]]
    assert versions == [1]  # bob doesn't see v2 (private)


async def test_get_version_owner_sees_private(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).get("/api/v1/models/alice/elf-rf/versions/2")
    assert resp.status_code == 200


async def test_get_version_non_owner_404_private(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).get("/api/v1/models/alice/elf-rf/versions/2")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run, fail**

```bash
cd backend && uv run pytest tests/test_models_get.py -v
```

- [ ] **Step 3: Implement**

Append to `backend/app/routers/models_registry.py`:

```python
from app.schemas.model_registry import (
    ModelVersionList, ModelVersionRead, RegisteredModelRead,
)
from app.services.model_registry import resolve_registered_model


def _summary_query(rm_id, user, *, only_visible=True):
    visible = (
        ModelVersion.visibility == ModelVersionVisibility.PUBLIC
    ) | (ModelVersion.owner_id == user.id)
    if user.role == Role.ADMIN or not only_visible:
        visible = sa.true()
    return (
        select(
            func.max(ModelVersion.mlflow_version).label("latest_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_production_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.STAGING,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_staging_version"),
        )
        .where(ModelVersion.registered_model_id == rm_id, visible)
    )


@router.get("/{owner}/{name}", response_model=RegisteredModelRead)
async def get_model(owner: str, name: str, session, user) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user)
    summary = (await session.execute(_summary_query(rm.id, user))).one()
    return RegisteredModelRead(
        owner=owner,
        name=name,
        description=rm.description,
        tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )


@router.get("/{owner}/{name}/versions", response_model=ModelVersionList)
async def list_versions(owner, name, session, user) -> ModelVersionList:
    rm = await resolve_registered_model(owner, name, session, user)
    visible = (
        ModelVersion.visibility == ModelVersionVisibility.PUBLIC
    ) | (ModelVersion.owner_id == user.id)
    if user.role == Role.ADMIN:
        visible = sa.true()
    versions = (await session.execute(
        select(ModelVersion)
        .where(ModelVersion.registered_model_id == rm.id, visible)
        .order_by(ModelVersion.mlflow_version.desc())
    )).scalars().all()
    items = [ModelVersionRead.model_validate(v) for v in versions]
    return ModelVersionList(items=items, total=len(items), page=1, page_size=len(items))


@router.get("/{owner}/{name}/versions/{version}", response_model=ModelVersionRead)
async def get_version(owner, name, version: int, session, user) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user)
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.registered_model_id == rm.id,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")
    is_owner = mv.owner_id == user.id
    is_admin = user.role.value == "admin"
    if mv.visibility == ModelVersionVisibility.PRIVATE and not (is_owner or is_admin):
        raise HTTPException(404, "version not found")
    return ModelVersionRead.model_validate(mv)
```

- [ ] **Step 4: Run, pass**

```bash
cd backend && uv run pytest tests/test_models_get.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_get.py
git commit -m "feat(backend): add GET endpoints for namespaced model summary + versions"
```

---

## Task 10: PATCH visibility endpoint

**Files:**

- Modify: `backend/app/routers/models_registry.py`
- Create: `backend/tests/test_models_visibility_patch.py`

- [ ] **Step 1: Tests**

```python
# test_models_visibility_patch.py
async def test_owner_can_toggle(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf/versions/2/visibility",
        json={"visibility": "public", "comment": "ready"},
    )
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "public"


async def test_non_owner_403(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).patch(
        "/api/v1/models/alice/elf-rf/versions/1/visibility",
        json={"visibility": "private"},
    )
    assert resp.status_code == 403


async def test_admin_overrides(populated, client_as, make_user):
    admin = await make_user(handle="admin", role="admin")
    resp = await client_as(admin).patch(
        "/api/v1/models/alice/elf-rf/versions/1/visibility",
        json={"visibility": "private"},
    )
    assert resp.status_code == 200


async def test_writes_audit_log(populated, client_as, session):
    from app.models import ModelVisibilityLog
    alice, _ = populated
    await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf/versions/1/visibility",
        json={"visibility": "private", "comment": "rollback"},
    )
    rows = (await session.execute(
        sa.select(ModelVisibilityLog)
    )).scalars().all()
    assert any(r.comment == "rollback" for r in rows)


async def test_noop_no_log(populated, client_as, session):
    from app.models import ModelVisibilityLog
    alice, _ = populated
    # v1 is already public; toggle to public again
    await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf/versions/1/visibility",
        json={"visibility": "public"},
    )
    count = (await session.execute(
        sa.select(sa.func.count()).select_from(ModelVisibilityLog)
    )).scalar()
    assert count == 0
```

- [ ] **Step 2: Run, fail**

```bash
cd backend && uv run pytest tests/test_models_visibility_patch.py -v
```

- [ ] **Step 3: Implement**

Append to `backend/app/routers/models_registry.py`:

```python
from app.models import ModelVisibilityLog
from app.schemas.model_registry import ModelVersionVisibilityUpdate


@router.patch(
    "/{owner}/{name}/versions/{version}/visibility",
    response_model=ModelVersionRead,
)
async def update_visibility(
    owner: str, name: str, version: int,
    body: ModelVersionVisibilityUpdate,
    session, user,
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.registered_model_id == rm.id,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")

    if mv.visibility == body.visibility:
        return ModelVersionRead.model_validate(mv)

    session.add(ModelVisibilityLog(
        model_version_id=mv.id,
        from_visibility=mv.visibility,
        to_visibility=body.visibility,
        actor_id=user.id,
        comment=body.comment,
    ))
    mv.visibility = body.visibility
    await session.commit()
    await session.refresh(mv)
    return ModelVersionRead.model_validate(mv)
```

- [ ] **Step 4: Run, pass**

```bash
cd backend && uv run pytest tests/test_models_visibility_patch.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_visibility_patch.py
git commit -m "feat(backend): PATCH /models/{owner}/{name}/versions/{version}/visibility"
```

---

## Task 11: PATCH description / tags endpoint

**Files:**

- Modify: `backend/app/routers/models_registry.py`
- Create: `backend/tests/test_models_metadata_patch.py`

- [ ] **Step 1: Tests**

```python
async def test_owner_updates_description(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf",
        json={"description": "## Random Forest classifier\n\nELF malware detection."},
    )
    assert resp.status_code == 200
    assert "Random Forest" in resp.json()["description"]


async def test_owner_updates_tags(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf",
        json={"tags": {"framework": "sklearn", "contract": "sample_csv"}},
    )
    assert resp.json()["tags"]["framework"] == "sklearn"


async def test_tags_rejects_non_string_value(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf",
        json={"tags": {"x": 123}},  # int not str
    )
    assert resp.status_code == 422


async def test_tags_rejects_nested(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf",
        json={"tags": {"x": {"y": "z"}}},  # nested
    )
    assert resp.status_code == 422


async def test_non_owner_403(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).patch(
        "/api/v1/models/alice/elf-rf",
        json={"description": "hijack"},
    )
    assert resp.status_code == 403


async def test_description_max_length(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf",
        json={"description": "x" * 5001},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run, fail**

```bash
cd backend && uv run pytest tests/test_models_metadata_patch.py -v
```

- [ ] **Step 3: Implement**

Append to `backend/app/routers/models_registry.py`:

```python
from app.schemas.model_registry import RegisteredModelUpdate


@router.patch("/{owner}/{name}", response_model=RegisteredModelRead)
async def update_model(
    owner: str, name: str, body: RegisteredModelUpdate,
    session, user,
) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    if body.description is not None:
        rm.description = body.description
    if body.tags is not None:
        # Pydantic dict[str, str] already validates value types; this is defensive.
        for k, v in body.tags.items():
            if not isinstance(v, str):
                raise HTTPException(422, f"tag value for '{k}' must be string")
        rm.tags = body.tags
    await session.commit()
    await session.refresh(rm)
    summary = (await session.execute(_summary_query(rm.id, user))).one()
    return RegisteredModelRead(
        owner=owner, name=name,
        description=rm.description, tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )
```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_metadata_patch.py
git commit -m "feat(backend): PATCH /models/{owner}/{name} for description + tags"
```

---

## Task 12: PATCH owner transfer endpoint

**Files:**

- Modify: `backend/app/routers/models_registry.py`
- Create: `backend/tests/test_models_owner_transfer.py`

- [ ] **Step 1: Tests**

```python
async def test_owner_transfers_succeeds(populated, client_as, session):
    alice, bob = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "bob", "comment": "handing off"},
    )
    assert resp.status_code == 200
    assert resp.json()["owner"] == "bob"


async def test_writes_audit_log(populated, client_as, session):
    from app.models import ModelOwnerTransferLog
    alice, _ = populated
    await client_as(alice).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "bob", "comment": "test"},
    )
    rows = (await session.execute(
        sa.select(ModelOwnerTransferLog)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].comment == "test"


async def test_calls_mlflow_rename(populated, client_as, mock_mlflow):
    alice, _ = populated
    await client_as(alice).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "bob"},
    )
    calls = [c for c in mock_mlflow.calls if "rename" in c.path]
    assert any(
        c.body.get("name") == "alice/elf-cnn"
        and c.body.get("new_name") == "bob/elf-cnn"
        for c in calls
    )


async def test_collision_409(populated, client_as):
    # both alice and bob own elf-rf in `populated`
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-rf/owner",
        json={"new_owner_handle": "bob"},
    )
    assert resp.status_code == 409


async def test_target_user_not_found_422(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "ghost"},
    )
    assert resp.status_code == 422


async def test_self_transfer_422(populated, client_as):
    alice, _ = populated
    resp = await client_as(alice).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "alice"},
    )
    assert resp.status_code == 422


async def test_non_owner_403(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).patch(
        "/api/v1/models/alice/elf-cnn/owner",
        json={"new_owner_handle": "bob"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

```python
from app.models import ModelOwnerTransferLog
from app.schemas.model_registry import OwnerTransferRequest
from app.services.mlflow_client import MlflowClient
from app.deps import get_mlflow_client  # add if missing — wrap singleton in deps.py


@router.patch("/{owner}/{name}/owner", response_model=RegisteredModelRead)
async def transfer_owner(
    owner: str, name: str, body: OwnerTransferRequest,
    session, user,
    client: Annotated[MlflowClient, Depends(get_mlflow_client)],
) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)

    new_owner = (await session.execute(
        select(User).where(User.handle == body.new_owner_handle)
    )).scalar_one_or_none()
    if new_owner is None:
        raise HTTPException(422, f"user '{body.new_owner_handle}' not found")
    if new_owner.id == rm.owner_id:
        raise HTTPException(422, "new owner is current owner")

    collision = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.owner_id == new_owner.id,
            RegisteredModel.detector_id == rm.detector_id,
        )
    )).scalar_one_or_none()
    if collision is not None:
        raise HTTPException(
            409,
            f"'{body.new_owner_handle}' already owns a model for this detector",
        )

    old_owner_id = rm.owner_id
    old_mlflow_name = rm.mlflow_name  # uses current owner.handle
    rm.owner_id = new_owner.id
    new_mlflow_name = f"{new_owner.handle}/{rm.detector.name}"

    await client.rename_registered_model(old_mlflow_name, new_mlflow_name)

    session.add(ModelOwnerTransferLog(
        registered_model_id=rm.id,
        from_owner_id=old_owner_id,
        to_owner_id=new_owner.id,
        actor_id=user.id,
        comment=body.comment,
    ))
    await session.commit()
    await session.refresh(rm)
    summary = (await session.execute(_summary_query(rm.id, user))).one()
    return RegisteredModelRead(
        owner=new_owner.handle, name=name,
        description=rm.description, tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )
```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_owner_transfer.py
git commit -m "feat(backend): PATCH /models/{owner}/{name}/owner — transfer with MLflow rename + audit log"
```

---

## Task 13: DELETE endpoints (registered_model + version)

**Files:**

- Modify: `backend/app/routers/models_registry.py`
- Create: `backend/tests/test_models_delete.py`

- [ ] **Step 1: Tests**

```python
import sqlalchemy as sa

from app.models import (
    Detector, ModelVersion, RegisteredModel, User,
)


async def _alice_elf_rf_rm_id(session) -> str:
    return (await session.execute(
        sa.select(RegisteredModel.id)
        .join(User, RegisteredModel.owner_id == User.id)
        .join(Detector, RegisteredModel.detector_id == Detector.id)
        .where(User.handle == "alice", Detector.name == "elf-rf")
    )).scalar_one()


async def test_delete_model_cascades(populated, client_as, session, mock_mlflow):
    alice, _ = populated
    rm_id_before = await _alice_elf_rf_rm_id(session)
    resp = await client_as(alice).delete("/api/v1/models/alice/elf-rf")
    assert resp.status_code == 204

    # DB cascade — RM gone, versions referencing it gone
    rm = await session.get(RegisteredModel, rm_id_before)
    assert rm is None
    leftover = (await session.execute(
        sa.select(ModelVersion).where(ModelVersion.registered_model_id == rm_id_before)
    )).scalars().all()
    assert leftover == []

    # MLflow cascade
    deletes = [c for c in mock_mlflow.calls if "registered-models/delete" in c.path]
    assert any(c.body.get("name") == "alice/elf-rf" for c in deletes)


async def test_delete_model_non_owner_403(populated, client_as):
    _, bob = populated
    resp = await client_as(bob).delete("/api/v1/models/alice/elf-rf")
    assert resp.status_code == 403


async def test_delete_version_keeps_others(populated, client_as, session, mock_mlflow):
    alice, _ = populated
    rm_id = await _alice_elf_rf_rm_id(session)
    resp = await client_as(alice).delete("/api/v1/models/alice/elf-rf/versions/2")
    assert resp.status_code == 204
    versions = (await session.execute(
        sa.select(ModelVersion).where(ModelVersion.registered_model_id == rm_id)
    )).scalars().all()
    nums = sorted(v.mlflow_version for v in versions)
    assert 2 not in nums
    assert 1 in nums
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement**

```python
@router.delete("/{owner}/{name}", status_code=204)
async def delete_model(owner, name, session, user, client) -> None:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mlflow_name = rm.mlflow_name
    await client.delete_registered_model(mlflow_name)
    await session.delete(rm)
    await session.commit()


@router.delete("/{owner}/{name}/versions/{version}", status_code=204)
async def delete_version(owner, name, version: int, session, user, client) -> None:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.registered_model_id == rm.id,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")
    await client.delete_model_version(rm.mlflow_name, str(version))
    await session.delete(mv)
    await session.commit()
```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_models_delete.py
git commit -m "feat(backend): DELETE endpoints with MLflow + DB cascade"
```

---

## Task 14: Stage transition endpoint — URL update

The existing `transition_stage` endpoint stays semantically; only the URL pattern changes from `/{name}/versions/{version}/transition` to `/{owner}/{name}/versions/{version}/transition`. Existing logic uses `mlflow_name` (string) — replace with the resolver.

**Files:**

- Modify: `backend/app/routers/models_registry.py` (find existing transition impl)

- [ ] **Step 1: Locate existing impl**

```bash
grep -n "transition" backend/app/routers/models_registry.py
```

- [ ] **Step 2: Adapt to new URL + resolver**

Replace the existing transition route:

```python
from app.models import ModelTransitionLog
from app.schemas.model_registry import ModelTransitionRequest
from app.services.model_registry import (
    InvalidTransitionError, validate_transition,
)


@router.post(
    "/{owner}/{name}/versions/{version}/transition",
    response_model=ModelVersionRead,
)
async def transition_stage(
    owner: str, name: str, version: int,
    body: ModelTransitionRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow_client)],
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.registered_model_id == rm.id,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")

    is_owner = mv.owner_id == user.id
    try:
        validate_transition(
            mv.current_stage, body.to_stage,
            actor_role=user.role.value, is_owner=is_owner,
        )
    except InvalidTransitionError as e:
        raise HTTPException(403, str(e))

    archive_existing = body.to_stage == ModelVersionStage.PRODUCTION
    await client.transition_model_version_stage(
        name=rm.mlflow_name,
        version=str(mv.mlflow_version),
        stage=body.to_stage.value,
        archive_existing_versions=archive_existing,
    )

    from_stage = mv.current_stage
    mv.current_stage = body.to_stage
    if archive_existing:
        await session.execute(
            sa.update(ModelVersion)
            .where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.id != mv.id,
                ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
            )
            .values(current_stage=ModelVersionStage.ARCHIVED)
        )
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
```

> Replaces the existing `transition_stage` impl at `routers/models_registry.py:209-316`. Logic (validate_transition / auto-archive other Production / write log / call MLflow) is preserved — only the URL signature, the resolver path, and the use of `rm.mlflow_name` differ.

- [ ] **Step 3: Update existing tests**

In `backend/tests/test_services_model_registry.py` and any transition-related test, update URL strings from `/api/v1/models/{name}/...` to `/api/v1/models/{owner}/{name}/...`.

- [ ] **Step 4: Run all transition tests**

```bash
cd backend && uv run pytest tests/test_services_model_registry.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/models_registry.py backend/tests/test_services_model_registry.py
git commit -m "refactor(backend): move stage transition endpoint under /{owner}/{name}/ namespace"
```

---

## Task 15: Reconciler — `_register_model_from_job` rewrite

Upserts `RegisteredModel`, uses namespaced `mlflow_name`, sets default visibility.

**Files:**

- Modify: `backend/app/reconciler/jobs.py`
- Create: `backend/tests/test_reconciler_model_registration.py`

- [ ] **Step 1: Tests**

```python
async def test_first_train_creates_registered_model(session, make_user, make_detector, make_train_job, mock_mlflow):
    alice = await make_user(handle="alice", role="developer")
    det = await make_detector(name="elf-rf", owner=alice)
    job = await make_train_job(owner=alice, detector=det, mlflow_run_id="r1")
    mock_mlflow.set_response(
        "/api/2.0/mlflow/model-versions/create",
        {"model_version": {"version": "1"}},
    )

    from app.reconciler.jobs import _register_model_from_job
    from app.services.mlflow_client import MlflowClient
    await _register_model_from_job(session, MlflowClient(base_url="x"), job)
    await session.commit()

    from app.models import RegisteredModel, ModelVersion
    rm = (await session.execute(
        sa.select(RegisteredModel).where(
            RegisteredModel.owner_id == alice.id,
            RegisteredModel.detector_id == det.id,
        )
    )).scalar_one()
    assert rm is not None
    mv = (await session.execute(sa.select(ModelVersion))).scalar_one()
    assert mv.visibility.value == "private"
    assert mv.registered_model_id == rm.id


async def test_second_train_reuses_registered_model(session, make_user, make_detector, make_train_job, mock_mlflow):
    alice = await make_user(handle="alice", role="developer")
    det = await make_detector(name="elf-rf", owner=alice)
    job1 = await make_train_job(owner=alice, detector=det, mlflow_run_id="r1")
    job2 = await make_train_job(owner=alice, detector=det, mlflow_run_id="r2")

    from app.reconciler.jobs import _register_model_from_job
    from app.services.mlflow_client import MlflowClient
    client = MlflowClient(base_url="x")

    mock_mlflow.set_response("/api/2.0/mlflow/model-versions/create", {"model_version": {"version": "1"}})
    await _register_model_from_job(session, client, job1)
    mock_mlflow.set_response("/api/2.0/mlflow/model-versions/create", {"model_version": {"version": "2"}})
    await _register_model_from_job(session, client, job2)
    await session.commit()

    from app.models import RegisteredModel
    rms = (await session.execute(sa.select(RegisteredModel))).scalars().all()
    assert len(rms) == 1


async def test_two_users_train_same_detector_get_separate_namespaces(
    session, make_user, make_detector, make_train_job, mock_mlflow,
):
    from app.models import RegisteredModel
    from app.reconciler.jobs import _register_model_from_job
    from app.services.mlflow_client import MlflowClient

    alice = await make_user(handle="alice", role="developer")
    bob = await make_user(handle="bob", role="developer")
    det = await make_detector(name="elf-rf", owner=alice)
    j_alice = await make_train_job(owner=alice, detector=det, mlflow_run_id="r-a")
    j_bob = await make_train_job(owner=bob, detector=det, mlflow_run_id="r-b")
    client = MlflowClient(base_url="http://mock")

    mock_mlflow.set_response("/api/2.0/mlflow/model-versions/create", {"model_version": {"version": "1"}})
    await _register_model_from_job(session, client, j_alice)
    mock_mlflow.set_response("/api/2.0/mlflow/model-versions/create", {"model_version": {"version": "1"}})
    await _register_model_from_job(session, client, j_bob)
    await session.commit()

    rms = (await session.execute(sa.select(RegisteredModel))).scalars().all()
    assert len(rms) == 2

    create_calls = [c for c in mock_mlflow.calls if "registered-models/create" in c.path]
    names = sorted(c.body["name"] for c in create_calls)
    assert names == ["alice/elf-rf", "bob/elf-rf"]
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace `_register_model_from_job`**

In `backend/app/reconciler/jobs.py`, replace the function (around lines 291-329):

```python
async def _register_model_from_job(
    session: AsyncSession, client: MlflowClient, j: Job
) -> None:
    from app.models import (
        Detector, DetectorVersion, ModelVersion,
        ModelVersionStage, ModelVersionVisibility,
        RegisteredModel, User,
    )

    if j.mlflow_run_id is None:
        raise RuntimeError(
            f"job {j.id} reached model registration without mlflow_run_id"
        )
    dv = await session.get(DetectorVersion, j.detector_version_id)
    if dv is None:
        raise RuntimeError(f"missing DetectorVersion {j.detector_version_id}")
    det = await session.get(Detector, dv.detector_id)
    if det is None:
        raise RuntimeError(f"missing Detector {dv.detector_id}")
    owner = await session.get(User, j.owner_id)
    if owner is None:
        raise RuntimeError(f"missing User {j.owner_id}")

    # Upsert RegisteredModel for (owner, detector)
    rm = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.owner_id == owner.id,
            RegisteredModel.detector_id == det.id,
        )
    )).scalar_one_or_none()
    if rm is None:
        rm = RegisteredModel(owner_id=owner.id, detector_id=det.id)
        session.add(rm)
        await session.flush()

    mlflow_name = f"{owner.handle}/{det.name}"
    await client.create_registered_model(mlflow_name)
    mv_resp = await client.create_model_version(
        name=mlflow_name,
        source=f"runs:/{j.mlflow_run_id}/model",
        run_id=j.mlflow_run_id,
    )

    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=int(mv_resp["version"]),
        mlflow_run_id=j.mlflow_run_id,
        current_stage=ModelVersionStage.NONE,
        visibility=ModelVersionVisibility.PRIVATE,
        detector_version_id=j.detector_version_id,
        source_job_id=j.id,
        owner_id=j.owner_id,
    )
    session.add(mv)
```

- [ ] **Step 4: Run reconciler tests, pass**

```bash
cd backend && uv run pytest tests/test_reconciler_model_registration.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/reconciler/jobs.py backend/tests/test_reconciler_model_registration.py
git commit -m "feat(backend): reconciler upserts RegisteredModel + uses namespaced mlflow_name"
```

---

## Task 16: Predict job validation helper

Mirrors `_load_dataset` pattern in `jobs.py`.

**Files:**

- Modify: `backend/app/routers/jobs.py`
- Modify: `backend/tests/test_jobs.py` (extend)

- [ ] **Step 1: Tests (extend existing test_jobs.py)**

```python
import sqlalchemy as sa

from app.models import (
    DetectorVersion, ModelVersion, ModelVersionVisibility,
)


async def _ids_for_predict(session, populated):
    """Return (alice_private_mv_id, alice_public_mv_id, alices_dv_id)."""
    alice, _ = populated
    private = (await session.execute(
        sa.select(ModelVersion.id).where(
            ModelVersion.owner_id == alice.id,
            ModelVersion.visibility == ModelVersionVisibility.PRIVATE,
        ).limit(1)
    )).scalar_one()
    public = (await session.execute(
        sa.select(ModelVersion.id).where(
            ModelVersion.owner_id == alice.id,
            ModelVersion.visibility == ModelVersionVisibility.PUBLIC,
        ).limit(1)
    )).scalar_one()
    dv = (await session.execute(
        sa.select(DetectorVersion.id)
        .join(ModelVersion, ModelVersion.detector_version_id == DetectorVersion.id)
        .where(ModelVersion.id == public).limit(1)
    )).scalar_one()
    return private, public, dv


async def test_predict_with_private_model_non_owner_422(client_as, populated, session, make_dataset):
    _, bob = populated
    private_mv, _, dv = await _ids_for_predict(session, populated)
    ds = await make_dataset(owner=bob, visibility="public")
    resp = await client_as(bob).post("/api/v1/jobs", json={
        "type": "predict",
        "detector_version_id": str(dv),
        "source_model_version_id": str(private_mv),
        "predict_dataset_id": str(ds.id),
    })
    assert resp.status_code == 422
    assert "not accessible" in resp.json()["detail"]


async def test_predict_with_public_model_any_user_succeeds(client_as, populated, session, make_dataset):
    _, bob = populated
    _, public_mv, dv = await _ids_for_predict(session, populated)
    ds = await make_dataset(owner=bob, visibility="public")
    resp = await client_as(bob).post("/api/v1/jobs", json={
        "type": "predict",
        "detector_version_id": str(dv),
        "source_model_version_id": str(public_mv),
        "predict_dataset_id": str(ds.id),
    })
    assert resp.status_code == 201


async def test_predict_with_private_model_owner_succeeds(client_as, populated, session, make_dataset):
    alice, _ = populated
    private_mv, _, dv = await _ids_for_predict(session, populated)
    ds = await make_dataset(owner=alice, visibility="public")
    resp = await client_as(alice).post("/api/v1/jobs", json={
        "type": "predict",
        "detector_version_id": str(dv),
        "source_model_version_id": str(private_mv),
        "predict_dataset_id": str(ds.id),
    })
    assert resp.status_code == 201


async def test_train_against_any_detector_no_403(client_as, populated, session, make_dataset):
    """Section 1.4 was rejected — any user can train against any detector."""
    _, bob = populated
    _, _, dv = await _ids_for_predict(session, populated)  # alice's detector_version
    ds = await make_dataset(owner=bob, visibility="public")
    resp = await client_as(bob).post("/api/v1/jobs", json={
        "type": "train",
        "detector_version_id": str(dv),
        "train_dataset_id": str(ds.id),
    })
    # 201 happy path; 422 if other validation (e.g. manifest stage) fails;
    # critically NOT 403 — that's what Section 1.4 would have produced.
    assert resp.status_code != 403
```

> `make_dataset` is an existing conftest fixture (or add a small one wrapping `DatasetConfig` creation).

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Add helper + use it in `_create_job`**

Add to `backend/app/routers/jobs.py`:

```python
async def _load_model_version_for_predict(
    mv_id: uuid.UUID | None, session: AsyncSession, user: User,
) -> ModelVersion | None:
    if mv_id is None:
        return None
    mv = await session.get(ModelVersion, mv_id)
    if mv is None:
        raise HTTPException(422, "source_model_version not found")
    if (
        mv.visibility == ModelVersionVisibility.PRIVATE
        and mv.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise HTTPException(422, "source_model_version not accessible")
    return mv
```

Replace existing source_model lookup (around `jobs.py:138-143`):

```python
# OLD: source_model = await session.get(ModelVersion, body.source_model_version_id) if body.source_model_version_id else None
source_model = await _load_model_version_for_predict(
    body.source_model_version_id, session, user
)
```

Add `from app.models import ModelVersionVisibility` to imports.

- [ ] **Step 4: Run, pass**

```bash
cd backend && uv run pytest tests/test_jobs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/jobs.py backend/tests/test_jobs.py
git commit -m "feat(backend): predict job validates source_model_version visibility"
```

---

## Task 17: Conftest fixtures + final test run

Consolidate the test fixtures referenced across Tasks 8–16.

**Files:**

- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Add fixtures**

Append to `backend/tests/conftest.py`:

```python
import pytest
from app.models import (
    Detector, DetectorVersion, Job, JobStatus, JobType,
    ModelVersion, ModelVersionStage, ModelVersionVisibility,
    RegisteredModel, Role, User,
)


@pytest.fixture
def make_user(session):
    async def _make(*, handle, role="developer", email=None):
        u = User(
            email=email or f"{handle}@test.local",
            handle=handle,
            role=Role(role),
        )
        session.add(u)
        await session.flush()
        return u
    return _make


@pytest.fixture
def make_detector(session):
    async def _make(*, name, owner, git_url=None):
        d = Detector(
            name=name,
            display_name=name.upper(),
            git_url=git_url or f"https://github.com/test/{name}",
            owner_id=owner.id,
        )
        session.add(d)
        await session.flush()
        return d
    return _make


@pytest.fixture
def make_registered_model_with_versions(session):
    async def _make(*, owner, detector, versions):
        # Need a DetectorVersion + Job to satisfy FKs
        dv = DetectorVersion(detector_id=detector.id, git_tag="v1", image_uri="x")
        session.add(dv)
        await session.flush()
        rm = RegisteredModel(owner_id=owner.id, detector_id=detector.id)
        session.add(rm)
        await session.flush()
        for v_num, vis, stage in versions:
            j = Job(
                type=JobType.TRAIN, owner_id=owner.id,
                detector_version_id=dv.id, status=JobStatus.SUCCEEDED,
                mlflow_run_id=f"r-{v_num}",
            )
            session.add(j)
            await session.flush()
            mv = ModelVersion(
                registered_model_id=rm.id,
                mlflow_version=v_num,
                mlflow_run_id=f"r-{v_num}",
                current_stage=stage,
                visibility=vis,
                detector_version_id=dv.id,
                source_job_id=j.id,
                owner_id=owner.id,
            )
            session.add(mv)
        await session.flush()
        return rm
    return _make


@pytest.fixture
def make_train_job(session):
    async def _make(*, owner, detector, mlflow_run_id):
        dv_id = (await session.execute(
            sa.select(DetectorVersion.id).where(DetectorVersion.detector_id == detector.id)
        )).scalar_one_or_none()
        if dv_id is None:
            dv = DetectorVersion(detector_id=detector.id, git_tag="v1", image_uri="x")
            session.add(dv)
            await session.flush()
            dv_id = dv.id
        j = Job(
            type=JobType.TRAIN, owner_id=owner.id,
            detector_version_id=dv_id, status=JobStatus.SUCCEEDED,
            mlflow_run_id=mlflow_run_id,
        )
        session.add(j)
        await session.flush()
        return j
    return _make


@pytest.fixture
def client_as(client, mock_cf_jwt):
    """Return a callable that returns an httpx client authed as the given user."""
    def _as(user):
        mock_cf_jwt({"email": user.email, "sub": str(user.id)})
        return client
    return _as
```

- [ ] **Step 2: Run all backend tests**

```bash
cd backend && uv run pytest -v
```

Expected: full suite green. Should be hundreds of tests; existing + ~50 new.

- [ ] **Step 3: Run lint + typecheck**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test(backend): add namespace + visibility test fixtures"
```

---

## Task 18: Open PR-A

- [ ] **Step 1: Push branch**

```bash
git checkout -b feat/model-registry-namespace-pr-a-backend
git push -u origin feat/model-registry-namespace-pr-a-backend
```

(If you've been committing on `main` locally, instead: `git checkout -b feat/model-registry-namespace-pr-a-backend` from the current head, then `git push -u origin feat/...`. Then `git checkout main && git reset --hard origin/main` to clean main — only if confirmed with operator.)

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "feat(backend): model registry namespace + per-version visibility (PR-A)" \
  --body "$(cat <<'EOF'
## Summary

Backend half of the model registry rebuild. URL pattern moves from
`/api/v1/models/{name}/...` to `/api/v1/models/{owner}/{name}/...` (GitHub-style
namespace). Every `ModelVersion` now carries `visibility` (default `private`).
Description / tags / owner-transfer / cascade-delete endpoints added.

**Spec:** docs/superpowers/specs/2026-05-07-model-registry-namespace-and-visibility-design.md
**Plan:** docs/superpowers/plans/2026-05-07-model-registry-namespace-and-visibility.md
**Phase:** A (PR-A)
**Pairs with:** PR-B frontend (separate PR; deploys together)

## Test plan

- [x] `uv run pytest` — full suite green
- [x] `uv run ruff check . && uv run ruff format --check .`
- [x] `uv run mypy`
- [x] Migration applies cleanly against fresh aiosqlite
- [x] Migration `downgrade()` reverses cleanly
- [ ] Operator review: pre-deploy checklist (spec §4.3) — wipe before merge

## Breaking changes (authorised in spec)

- `ModelVersion.mlflow_name` column dropped — replaced by FK `registered_model_id`.
- All `/api/v1/models/{name}/...` URLs removed; replaced by `/api/v1/models/{owner}/{name}/...`.
- Existing `model_version` rows must be wiped before this migration runs.
- New required `User.handle` column auto-derived from email at migration time and on first login.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI green, address review**

CI runs `pre-commit run --all-files` + backend tests + helm lint per `.github/workflows/`. Address any feedback. Merge when approved.

---

# Phase B — PR-B: Frontend

Open as branch `feat/model-registry-namespace-pr-b-frontend` after PR-A merges. First step regenerates types from PR-A's deployed OpenAPI.

## Task 19: Regenerate API types

**Files:**

- Modify: `frontend/src/api/schema.gen.ts` (auto)

- [ ] **Step 1: Pull latest main**

```bash
git checkout main && git pull
git checkout -b feat/model-registry-namespace-pr-b-frontend
```

- [ ] **Step 2: Regenerate types**

```bash
cd frontend && pnpm gen-api-types
```

(Runs `frontend/scripts/gen-api-types.sh` which hits the running backend's `/openapi.json`. PR-A backend must be reachable — local dev backend OK.)

- [ ] **Step 3: Verify types compile**

```bash
cd frontend && pnpm typecheck
```

Expected: errors in existing files using old API shape (e.g. `_authed.models.$name.tsx`). These are intentional — Tasks 31–32 fix them.

- [ ] **Step 4: Commit type regen separately**

```bash
git add frontend/src/api/schema.gen.ts
git commit -m "chore(frontend): regenerate API types after PR-A merge"
```

(The errors from typecheck will resolve as we ship the rest of Phase B. No green-state needed yet.)

---

## Task 20: i18n keys

**Files:**

- Modify: `frontend/src/i18n/en.json`
- Modify: `frontend/src/i18n/zh-TW.json`

- [ ] **Step 1: Add keys to en.json**

Insert into top-level `models` object (alongside existing `stagesExplainer`, `stages`):

```jsonc
"models": {
  ... existing keys ...,
  "owner": "Owner",
  "filter": {
    "all": "All",
    "public": "Public",
    "mine": "Mine"
  },
  "visibility": {
    "public": "Public",
    "private": "Private",
    "publicTooltip": "Visible and usable by all authenticated users.",
    "privateTooltip": "Only you (owner) and admins can see or use this version.",
    "makePublic": "Make public",
    "makePrivate": "Make private",
    "warningPrivate": "Other users will lose access for new predict jobs. Existing jobs continue running.",
    "warningPublic": "All authenticated users will be able to view and use this version.",
    "changedToast": "Visibility updated"
  },
  "description": {
    "title": "Description",
    "edit": "Edit description",
    "placeholder": "Markdown supported. Document your model usage, training data, evaluation results...",
    "successToast": "Description updated",
    "empty": "No description yet."
  },
  "tags": {
    "title": "Tags",
    "edit": "Edit tags",
    "placeholder": "{ \"framework\": \"sklearn\", \"contract\": \"sample_csv\" }",
    "schemaError": "Tags must be a flat JSON object of string keys to string values",
    "successToast": "Tags updated",
    "empty": "No tags."
  },
  "transfer": {
    "title": "Transfer ownership",
    "description": "Move this model to another user. The new owner cannot already own a model for the same detector.",
    "newOwnerLabel": "New owner handle",
    "warning": "Other users may lose write access. Existing predict jobs continue.",
    "submit": "Transfer",
    "successToast": "Ownership transferred"
  },
  "delete": {
    "title": "Delete model",
    "warning": "This action permanently deletes the model and all versions, including MLflow artefacts. This cannot be undone.",
    "confirmPrompt": "To confirm, type {{fullName}} below",
    "successToast": "Model deleted"
  },
  "deleteVersion": {
    "title": "Delete version",
    "warning": "This will permanently delete version {{version}} of {{fullName}}.",
    "successToast": "Version deleted"
  }
}
```

- [ ] **Step 2: Add zh-TW.json equivalents**

Same structure, with translations: 公開 / 私有 / 改為公開 / 改為私有 / 編輯描述 / 編輯標籤 / 轉移擁有權 / 刪除模型 / 刪除版本. Use the spec §3.7 content as source of truth.

- [ ] **Step 3: Verify JSON validity**

```bash
cd frontend && python -m json.tool src/i18n/en.json > /dev/null && python -m json.tool src/i18n/zh-TW.json > /dev/null
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/
git commit -m "chore(frontend): add i18n keys for namespace + visibility UI"
```

---

## Task 21: Add `react-markdown` + `MarkdownView`

**Files:**

- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`
- Create: `frontend/src/components/common/MarkdownView.tsx`
- Create: `frontend/tests/unit/components/common/MarkdownView.test.tsx`

- [ ] **Step 1: Add dependency**

```bash
cd frontend && pnpm add react-markdown
```

- [ ] **Step 2: Write test**

`frontend/tests/unit/components/common/MarkdownView.test.tsx`:

````tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownView } from "@/components/common/MarkdownView";

describe("MarkdownView", () => {
  it("renders headings", () => {
    render(<MarkdownView source="## Heading" />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "Heading",
    );
  });

  it("renders code blocks", () => {
    render(<MarkdownView source="```\ncode\n```" />);
    const code = screen.getByText("code");
    expect(code.tagName).toBe("CODE");
  });

  it("renders lists", () => {
    render(<MarkdownView source="- a\n- b" />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("does not execute raw HTML", () => {
    const html = "<script>window.__pwned__=true</script>plain";
    render(<MarkdownView source={html} />);
    // react-markdown by default does not render raw HTML
    expect((window as any).__pwned__).toBeUndefined();
    expect(screen.getByText(/plain/)).toBeInTheDocument();
  });

  it("renders empty source as nothing", () => {
    const { container } = render(<MarkdownView source="" />);
    expect(container.textContent).toBe("");
  });
});
````

- [ ] **Step 3: Implement**

```tsx
// frontend/src/components/common/MarkdownView.tsx
import ReactMarkdown from "react-markdown";

interface Props {
  source: string;
  className?: string;
}

export function MarkdownView({ source, className }: Props) {
  return (
    <div
      className={`prose prose-sm dark:prose-invert max-w-none ${className ?? ""}`}
    >
      <ReactMarkdown>{source}</ReactMarkdown>
    </div>
  );
}
```

(Tailwind `prose` class assumes `@tailwindcss/typography` — if not installed, add it: `pnpm add -D @tailwindcss/typography`, then add to `tailwind.config.ts` plugins. Otherwise drop the class.)

- [ ] **Step 4: Run, pass**

```bash
cd frontend && pnpm test components/common/MarkdownView
```

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml frontend/src/components/common/MarkdownView.tsx frontend/tests/unit/components/common/MarkdownView.test.tsx
git commit -m "feat(frontend): add react-markdown + MarkdownView component"
```

---

## Task 22: VisibilityBadge component

**Files:**

- Create: `frontend/src/components/models/VisibilityBadge.tsx`
- Create: `frontend/tests/unit/components/models/VisibilityBadge.test.tsx`

- [ ] **Step 1: Test**

```tsx
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it } from "vitest";
import { VisibilityBadge } from "@/components/models/VisibilityBadge";
import i18n from "@/i18n";

const wrap = (ui: React.ReactNode) => (
  <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
);

describe("VisibilityBadge", () => {
  it("renders Public with Globe icon", () => {
    render(wrap(<VisibilityBadge visibility="public" />));
    expect(screen.getByText("Public")).toBeInTheDocument();
    expect(screen.getByLabelText(/globe/i)).toBeInTheDocument();
  });

  it("renders Private with Lock icon", () => {
    render(wrap(<VisibilityBadge visibility="private" />));
    expect(screen.getByText("Private")).toBeInTheDocument();
    expect(screen.getByLabelText(/lock/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement**

```tsx
// frontend/src/components/models/VisibilityBadge.tsx
import { Globe, Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

interface Props {
  visibility: "public" | "private";
  iconOnly?: boolean;
}

export function VisibilityBadge({ visibility, iconOnly }: Props) {
  const { t } = useTranslation();
  if (visibility === "public") {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-emerald-500 text-emerald-700 dark:text-emerald-400"
      >
        <Globe aria-label="globe" className="h-3 w-3" />
        {!iconOnly && t("models.visibility.public")}
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="gap-1 border-slate-400 text-slate-600 dark:text-slate-400"
    >
      <Lock aria-label="lock" className="h-3 w-3" />
      {!iconOnly && t("models.visibility.private")}
    </Badge>
  );
}
```

- [ ] **Step 3: Run, pass**

```bash
cd frontend && pnpm test components/models/VisibilityBadge
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/models/VisibilityBadge.tsx frontend/tests/unit/components/models/VisibilityBadge.test.tsx
git commit -m "feat(frontend): add VisibilityBadge"
```

---

## Task 23: OwnerLabel + remaining form components

Bundle the smaller display + dialog components into one task to keep velocity (each is < 30 lines).

**Files:**

- Create: `frontend/src/components/users/OwnerLabel.tsx`
- Create: `frontend/src/components/forms/ModelDescriptionEditor.tsx`
- Create: `frontend/src/components/forms/ModelTagsEditor.tsx`
- Create: `frontend/src/components/forms/OwnerTransferDialog.tsx`
- Create: `frontend/src/components/forms/DeleteModelDialog.tsx`
- Create: `frontend/src/components/forms/ModelVisibilityDialog.tsx`
- Create: matching `frontend/tests/unit/...` for each

- [ ] **Step 1: OwnerLabel**

```tsx
// components/users/OwnerLabel.tsx
import { User } from "lucide-react";

export function OwnerLabel({ handle }: { handle: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
      <User className="h-3 w-3" /> {handle}
    </span>
  );
}
```

Test: simple `expect(screen.getByText(/handle/)).toBeInTheDocument()`.

- [ ] **Step 2: ModelDescriptionEditor**

```tsx
// components/forms/ModelDescriptionEditor.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  initialValue: string | null;
  onClose: () => void;
  onSubmit: (description: string) => void;
}

export function ModelDescriptionEditor({
  open,
  initialValue,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initialValue ?? "");
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.description.edit")}</DialogTitle>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("models.description.placeholder")}
          rows={10}
          maxLength={5000}
        />
        <p className="text-xs text-muted-foreground">{value.length} / 5000</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(value)}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Test: opens, types, submits → callback fires with value.

- [ ] **Step 3: ModelTagsEditor**

```tsx
// components/forms/ModelTagsEditor.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

const TagsSchema = z.record(z.string(), z.string());

export function ModelTagsEditor({
  open,
  initialValue,
  onClose,
  onSubmit,
}: {
  open: boolean;
  initialValue: Record<string, string>;
  onClose: () => void;
  onSubmit: (tags: Record<string, string>) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(JSON.stringify(initialValue, null, 2));

  const handleSubmit = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch {
      toast.error(t("models.tags.schemaError"));
      return;
    }
    const result = TagsSchema.safeParse(parsed);
    if (!result.success) {
      toast.error(t("models.tags.schemaError"));
      return;
    }
    onSubmit(result.data);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.tags.edit")}</DialogTitle>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("models.tags.placeholder")}
          rows={10}
          className="font-mono text-sm"
        />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Test: type valid JSON → submit succeeds; nested → toast + no submit.

- [ ] **Step 4: OwnerTransferDialog**

```tsx
// components/forms/OwnerTransferDialog.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function OwnerTransferDialog({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (newOwner: string, comment: string | null) => void;
}) {
  const { t } = useTranslation();
  const [handle, setHandle] = useState("");
  const [comment, setComment] = useState("");
  const valid = handle.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.transfer.title")}</DialogTitle>
          <DialogDescription>
            {t("models.transfer.description")}
          </DialogDescription>
        </DialogHeader>
        <label className="text-sm">
          {t("models.transfer.newOwnerLabel")}
          <Input value={handle} onChange={(e) => setHandle(e.target.value)} />
        </label>
        <Textarea
          placeholder="Optional comment"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={3}
        />
        <p className="text-sm text-amber-600">{t("models.transfer.warning")}</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!valid}
            onClick={() => onSubmit(handle.trim(), comment || null)}
          >
            {t("models.transfer.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Test: handle empty → submit disabled; handle filled → submit enabled, callback receives.

- [ ] **Step 5: DeleteModelDialog**

```tsx
// components/forms/DeleteModelDialog.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";

export function DeleteModelDialog({
  open,
  owner,
  name,
  onClose,
  onConfirm,
}: {
  open: boolean;
  owner: string;
  name: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useState("");
  const fullName = `${owner}/${name}`;
  const matches = confirm === fullName;

  return (
    <AlertDialog open={open} onOpenChange={(o) => !o && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("models.delete.title")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("models.delete.warning")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <p className="text-sm">
          {t("models.delete.confirmPrompt", { fullName })}
        </p>
        <Input
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={fullName}
        />
        <AlertDialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={!matches} onClick={onConfirm}>
            Delete
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

Test: type wrong → button disabled; type matching → button enabled.

- [ ] **Step 6: ModelVisibilityDialog**

```tsx
// components/forms/ModelVisibilityDialog.tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

export function ModelVisibilityDialog({
  open,
  current,
  onClose,
  onSubmit,
}: {
  open: boolean;
  current: "public" | "private";
  onClose: () => void;
  onSubmit: (visibility: "public" | "private", comment: string | null) => void;
}) {
  const { t } = useTranslation();
  const [comment, setComment] = useState("");
  const target = current === "public" ? "private" : "public";
  const titleKey =
    target === "public"
      ? "models.visibility.makePublic"
      : "models.visibility.makePrivate";
  const warningKey =
    target === "public"
      ? "models.visibility.warningPublic"
      : "models.visibility.warningPrivate";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t(titleKey)}</DialogTitle>
          <DialogDescription>{t(warningKey)}</DialogDescription>
        </DialogHeader>
        <Textarea
          placeholder="Optional comment"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={3}
        />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(target, comment || null)}>
            {t(titleKey)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Test: current=public → button text "Make private"; submit → callback gets "private".

- [ ] **Step 7: Run all component tests**

```bash
cd frontend && pnpm test components/
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/users/OwnerLabel.tsx \
        frontend/src/components/forms/ModelDescriptionEditor.tsx \
        frontend/src/components/forms/ModelTagsEditor.tsx \
        frontend/src/components/forms/OwnerTransferDialog.tsx \
        frontend/src/components/forms/DeleteModelDialog.tsx \
        frontend/src/components/forms/ModelVisibilityDialog.tsx \
        frontend/tests/unit/components/
git commit -m "feat(frontend): add OwnerLabel + 5 model action dialogs"
```

---

## Task 24: TanStack Query mutations

**Files:**

- Modify: `frontend/src/api/queries/models.ts` (existing)

- [ ] **Step 1: Add mutations**

Append to `frontend/src/api/queries/models.ts`:

```typescript
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";

export function useUpdateModelDescription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      description,
    }: {
      owner: string;
      name: string;
      description: string;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}",
        {
          params: { path: { owner, name } },
          body: { description },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_d, { owner, name }) => {
      qc.invalidateQueries({ queryKey: ["models", owner, name] });
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useUpdateModelTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      tags,
    }: {
      owner: string;
      name: string;
      tags: Record<string, string>;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}",
        {
          params: { path: { owner, name } },
          body: { tags },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_d, { owner, name }) => {
      qc.invalidateQueries({ queryKey: ["models", owner, name] });
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useTransferOwner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      newOwner,
      comment,
    }: {
      owner: string;
      name: string;
      newOwner: string;
      comment: string | null;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}/owner",
        {
          params: { path: { owner, name } },
          body: { new_owner_handle: newOwner, comment },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useDeleteModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ owner, name }: { owner: string; name: string }) => {
      const { error } = await client.DELETE("/api/v1/models/{owner}/{name}", {
        params: { path: { owner, name } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useDeleteVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      version,
    }: {
      owner: string;
      name: string;
      version: number;
    }) => {
      const { error } = await client.DELETE(
        "/api/v1/models/{owner}/{name}/versions/{version}",
        {
          params: { path: { owner, name, version } },
        },
      );
      if (error) throw error;
    },
    onSuccess: (_d, { owner, name }) => {
      qc.invalidateQueries({ queryKey: ["models", owner, name] });
    },
  });
}

export function useUpdateVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      version,
      visibility,
      comment,
    }: {
      owner: string;
      name: string;
      version: number;
      visibility: "public" | "private";
      comment: string | null;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}/versions/{version}/visibility",
        {
          params: { path: { owner, name, version } },
          body: { visibility, comment },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_d, { owner, name }) => {
      qc.invalidateQueries({ queryKey: ["models", owner, name] });
    },
  });
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/queries/models.ts
git commit -m "feat(frontend): add 6 model registry mutations"
```

---

## Task 25: Models list page rewrite

**Files:**

- Modify: `frontend/src/routes/_authed.models._index.tsx`

- [ ] **Step 1: Replace file**

```tsx
import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { OwnerLabel } from "@/components/users/OwnerLabel";
import { VisibilityBadge } from "@/components/models/VisibilityBadge";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Models" };

interface ModelRow {
  owner: string;
  name: string;
  description: string | null;
  tags: Record<string, string>;
  latest_version: number | null;
  latest_production_version: number | null;
  latest_staging_version: number | null;
}

const columns: ColumnDef<ModelRow>[] = [
  {
    id: "model",
    header: "Model",
    cell: ({ row }) => (
      <Link
        to={`/models/${row.original.owner}/${row.original.name}`}
        className="hover:underline"
      >
        <OwnerLabel handle={row.original.owner} />
        <span className="ml-1 font-medium">/ {row.original.name}</span>
      </Link>
    ),
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (
      <span className="line-clamp-1 text-sm text-muted-foreground">
        {row.original.description?.slice(0, 80) ?? "—"}
      </span>
    ),
  },
  {
    accessorKey: "latest_version",
    header: "Latest",
    cell: ({ row }) => row.original.latest_version ?? "—",
  },
  {
    accessorKey: "latest_staging_version",
    header: "Staging",
    cell: ({ row }) =>
      row.original.latest_staging_version != null ? (
        <Badge variant="secondary">
          v{row.original.latest_staging_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "latest_production_version",
    header: "Production",
    cell: ({ row }) =>
      row.original.latest_production_version != null ? (
        <Badge className="bg-emerald-600">
          v{row.original.latest_production_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
];

export default function ModelsListPage() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<"all" | "public" | "mine">("all");
  const [ownerFilter, setOwnerFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["models", { filter, ownerFilter }],
    queryFn: async () => {
      const { data } = await client.GET("/api/v1/models", {
        params: {
          query: {
            visibility: filter,
            ...(ownerFilter ? { owner: ownerFilter } : {}),
          },
        },
      });
      return data ?? [];
    },
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Models"
        actions={
          <>
            <Input
              placeholder={t("models.owner")}
              value={ownerFilter}
              onChange={(e) => setOwnerFilter(e.target.value)}
              className="w-32"
            />
            <Select
              value={filter}
              onValueChange={(v) => setFilter(v as typeof filter)}
            >
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("models.filter.all")}</SelectItem>
                <SelectItem value="public">
                  {t("models.filter.public")}
                </SelectItem>
                <SelectItem value="mine">{t("models.filter.mine")}</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={data}
          columns={columns}
          emptyMessage="No models yet."
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/_authed.models._index.tsx
git commit -m "feat(frontend): rewrite Models list with namespace + visibility filter"
```

---

## Task 26: Models detail page rewrite + route rename

**Files:**

- Delete: `frontend/src/routes/_authed.models.$name.tsx`
- Create: `frontend/src/routes/_authed.models.$owner.$name.tsx`

- [ ] **Step 1: Move + rewrite**

```bash
git mv frontend/src/routes/_authed.models.\$name.tsx frontend/src/routes/_authed.models.\$owner.\$name.tsx
```

Then replace contents:

```tsx
import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, MoreVertical } from "lucide-react";
import { client } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { OwnerLabel } from "@/components/users/OwnerLabel";
import { VisibilityBadge } from "@/components/models/VisibilityBadge";
import { MarkdownView } from "@/components/common/MarkdownView";
import { ModelDescriptionEditor } from "@/components/forms/ModelDescriptionEditor";
import { ModelTagsEditor } from "@/components/forms/ModelTagsEditor";
import { OwnerTransferDialog } from "@/components/forms/OwnerTransferDialog";
import { DeleteModelDialog } from "@/components/forms/DeleteModelDialog";
import { ModelVisibilityDialog } from "@/components/forms/ModelVisibilityDialog";
import { ModelTransitionDialog } from "@/components/forms/ModelTransitionDialog";
import {
  useUpdateModelDescription,
  useUpdateModelTags,
  useTransferOwner,
  useDeleteModel,
  useDeleteVersion,
  useUpdateVisibility,
} from "@/api/queries/models";
import { toast } from "sonner";
import { formatRelative } from "@/lib/date";

export default function ModelDetailPage() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const summary = useQuery({
    queryKey: ["models", owner, name],
    queryFn: async () => {
      const { data } = await client.GET("/api/v1/models/{owner}/{name}", {
        params: { path: { owner: owner!, name: name! } },
      });
      return data;
    },
  });
  const versions = useQuery({
    queryKey: ["models", owner, name, "versions"],
    queryFn: async () => {
      const { data } = await client.GET(
        "/api/v1/models/{owner}/{name}/versions",
        { params: { path: { owner: owner!, name: name! } } },
      );
      return data?.items ?? [];
    },
  });
  const me = useQuery({
    queryKey: ["users", "me"],
    queryFn: async () => {
      const { data } = await client.GET("/api/v1/users/me");
      return data;
    },
  });

  const isOwnerOrAdmin = me.data?.handle === owner || me.data?.role === "admin";

  const [editDesc, setEditDesc] = useState(false);
  const [editTags, setEditTags] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const upDesc = useUpdateModelDescription();
  const upTags = useUpdateModelTags();
  const transfer = useTransferOwner();
  const del = useDeleteModel();
  // ... mutations wired below

  if (!summary.data) return <p>Loading…</p>;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/models">
          <ChevronLeft /> Back
        </Link>
      </Button>

      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl">
            <OwnerLabel handle={summary.data.owner} /> /{" "}
            <span className="font-bold">{summary.data.name}</span>
          </h1>
          <p className="text-sm text-muted-foreground">
            Created {formatRelative(summary.data.created_at)}
          </p>
        </div>
        {isOwnerOrAdmin && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon">
                <MoreVertical />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onClick={() => setEditDesc(true)}>
                {t("models.description.edit")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setEditTags(true)}>
                {t("models.tags.edit")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setTransferOpen(true)}>
                {t("models.transfer.title")}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => setDeleteOpen(true)}
              >
                {t("models.delete.title")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>

      <section>
        <h2 className="text-lg font-semibold">
          {t("models.description.title")}
        </h2>
        {summary.data.description ? (
          <MarkdownView source={summary.data.description} />
        ) : (
          <p className="text-muted-foreground">
            {t("models.description.empty")}
          </p>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold">{t("models.tags.title")}</h2>
        {Object.keys(summary.data.tags).length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.data.tags).map(([k, v]) => (
              <Badge key={k} variant="secondary">
                {k}={v as string}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground">{t("models.tags.empty")}</p>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold">Versions</h2>
        {/* version table — for each version, show stage badge + visibility badge + run + created + per-version dropdown */}
        {/* per-version actions: transition stage, toggle visibility, delete version. Wire each via the imported mutations + dialog state. Keep keyed dialogs (only one version's dialog open at a time). */}
        {/* Implementation note: maintain `activeVersion: number | null` state; opening a dialog sets it; submit closes + invalidates. */}
      </section>

      {/* Dialogs */}
      <ModelDescriptionEditor
        open={editDesc}
        initialValue={summary.data.description}
        onClose={() => setEditDesc(false)}
        onSubmit={async (description) => {
          await upDesc.mutateAsync({ owner: owner!, name: name!, description });
          setEditDesc(false);
          toast.success(t("models.description.successToast"));
        }}
      />
      <ModelTagsEditor
        open={editTags}
        initialValue={summary.data.tags}
        onClose={() => setEditTags(false)}
        onSubmit={async (tags) => {
          await upTags.mutateAsync({ owner: owner!, name: name!, tags });
          setEditTags(false);
          toast.success(t("models.tags.successToast"));
        }}
      />
      <OwnerTransferDialog
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
        onSubmit={async (newOwner, comment) => {
          await transfer.mutateAsync({
            owner: owner!,
            name: name!,
            newOwner,
            comment,
          });
          setTransferOpen(false);
          navigate(`/models/${newOwner}/${name}`);
          toast.success(t("models.transfer.successToast"));
        }}
      />
      <DeleteModelDialog
        open={deleteOpen}
        owner={owner!}
        name={name!}
        onClose={() => setDeleteOpen(false)}
        onConfirm={async () => {
          await del.mutateAsync({ owner: owner!, name: name! });
          setDeleteOpen(false);
          navigate("/models");
          toast.success(t("models.delete.successToast"));
        }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Implement the version table inline**

In the `Versions` section above, expand the placeholder. For each `version`:

```tsx
<table className="w-full text-sm">
  <thead>
    <tr>
      <th>Version</th>
      <th>Stage</th>
      <th>Visibility</th>
      <th>Run</th>
      <th>Created</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {versions.data?.map((v) => (
      <tr key={v.id} className="border-t">
        <td>v{v.mlflow_version}</td>
        <td>
          <Badge>{v.current_stage}</Badge>
        </td>
        <td>
          <VisibilityBadge visibility={v.visibility} />
        </td>
        <td>
          <code>{v.mlflow_run_id.slice(0, 8)}</code>
        </td>
        <td>{formatRelative(v.created_at)}</td>
        <td>
          {isOwnerOrAdmin && (
            <VersionActions
              owner={owner!}
              name={name!}
              version={v.mlflow_version}
              stage={v.current_stage}
              currentVisibility={v.visibility}
            />
          )}
        </td>
      </tr>
    ))}
  </tbody>
</table>
```

Where `VersionActions` is a small component within the same file that owns the per-version dialog state (transition / visibility / delete) and wires `useUpdateVisibility`, `useDeleteVersion`, and the existing transition mutation.

- [ ] **Step 3: Typecheck**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 4: Manual smoke test (when backend is running)**

```bash
cd frontend && pnpm dev
```

Open `http://localhost:5173/models` and click into a model.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/
git commit -m "feat(frontend): rewrite model detail page under /\$owner/\$name namespace"
```

---

## Task 27: Playwright E2E

**Files:**

- Create: `frontend/tests/e2e/model-visibility.spec.ts`
- Create: `frontend/tests/e2e/model-description-tags.spec.ts`
- Create: `frontend/tests/e2e/model-transfer-owner.spec.ts`
- Create: `frontend/tests/e2e/model-delete.spec.ts`
- Create: `frontend/tests/e2e/model-namespace-collision.spec.ts`
- Modify: `frontend/tests/e2e/phase11e-full-flow.spec.ts` (extend)

- [ ] **Step 1: Write `model-visibility.spec.ts`**

```typescript
import { test, expect } from "@playwright/test";
import { loginAs, trainElfRfModel } from "./helpers";

test.describe("model visibility", () => {
  test("owner toggles version private→public→private", async ({ browser }) => {
    const ctxA = await browser.newContext();
    const pageA = await ctxA.newPage();
    await loginAs(pageA, "userA");
    const { ownerHandle, modelName, version } = await trainElfRfModel(pageA);

    await pageA.goto(`/models/${ownerHandle}/${modelName}`);
    await expect(pageA.locator(`tr:has-text("v${version}")`)).toContainText(
      "Private",
    );

    // Toggle to public via kebab
    await pageA
      .locator(`tr:has-text("v${version}") [aria-label="more"]`)
      .click();
    await pageA.locator('text="Make public"').click();
    await pageA.locator('button:has-text("Make public")').click();
    await expect(pageA.locator(`tr:has-text("v${version}")`)).toContainText(
      "Public",
    );

    // Second user sees it
    const ctxB = await browser.newContext();
    const pageB = await ctxB.newPage();
    await loginAs(pageB, "userB");
    await pageB.goto("/models");
    await expect(
      pageB.locator(`text="${ownerHandle}/${modelName}"`),
    ).toBeVisible();

    // Owner toggles back
    await pageA
      .locator(`tr:has-text("v${version}") [aria-label="more"]`)
      .click();
    await pageA.locator('text="Make private"').click();
    await pageA.locator('button:has-text("Make private")').click();
    await expect(pageA.locator(`tr:has-text("v${version}")`)).toContainText(
      "Private",
    );

    // userB no longer sees
    await pageB.reload();
    await expect(
      pageB.locator(`text="${ownerHandle}/${modelName}"`),
    ).not.toBeVisible();
  });
});
```

- [ ] **Step 2: Write remaining 4 E2E specs** following the same pattern. Each spec is < 60 lines.

- [ ] **Step 3: Extend `phase11e-full-flow.spec.ts`**

Add an assertion after train job completes that the produced model version starts as Private (Lock icon visible in detail page).

- [ ] **Step 4: Run unit + E2E locally**

```bash
cd frontend && pnpm test && pnpm playwright test
```

Note: Playwright requires running backend. Skip if backend unavailable; run before PR.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/
git commit -m "test(frontend): playwright e2e for visibility, description/tags, transfer, delete, namespace"
```

---

## Task 28: Frontend integration check

- [ ] **Step 1: Full check**

```bash
cd frontend && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test
```

Expected: all green.

- [ ] **Step 2: Open PR-B**

```bash
git push -u origin feat/model-registry-namespace-pr-b-frontend
gh pr create \
  --title "feat(frontend): model registry namespace + per-version visibility (PR-B)" \
  --body "$(cat <<'EOF'
## Summary

Frontend half. Route refactor `_authed.models.$name.tsx → $owner.$name.tsx`,
7 new components (`OwnerLabel`, `VisibilityBadge`, `MarkdownView`, 5 dialogs),
6 TanStack Query mutations, list page filter dropdown + owner search, detail
page kebab menu (description / tags / transfer / delete) + per-version
dropdowns (transition / visibility / delete).

**Spec:** docs/superpowers/specs/2026-05-07-model-registry-namespace-and-visibility-design.md
**Plan:** docs/superpowers/plans/2026-05-07-model-registry-namespace-and-visibility.md
**Phase:** B (PR-B)
**Pairs with:** PR-A backend (must merge first)

## New dependency

- `react-markdown` ~30 KB gzip, MIT, mainstream HF/GitLab choice for safe markdown rendering.

## Test plan

- [x] `pnpm typecheck` clean
- [x] `pnpm lint` clean
- [x] `pnpm format:check` clean
- [x] `pnpm test` (vitest) green
- [ ] `pnpm playwright test` (run with PR-A backend deployed)
- [ ] Manual: list page filter (all/public/mine + owner search)
- [ ] Manual: detail page edit description/tags
- [ ] Manual: transfer ownership flow
- [ ] Manual: delete model double-confirm
- [ ] Manual: per-version visibility toggle

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# Phase C — Operations

## Task 29: Pre-deploy operator runbook

After PR-A and PR-B both merge, before deploy.

**Files:**

- Operator action only — no code changes in this task.

- [ ] **Step 1: Broadcast maintenance window** in Discord channel.

- [ ] **Step 2: Soft-delete existing detectors** in lolday UI:
  - `elfrfdet`
  - `elfcnndet`

- [ ] **Step 3: Wipe MLflow**

```bash
bash docs/runbooks/wipe-mlflow.md  # follow the runbook steps; involves MLflow gc + artefact removal
```

- [ ] **Step 4: Wipe lolday DB model state**

```bash
kubectl exec -n lolday deploy/backend -- psql "$DATABASE_URL" -c "
DELETE FROM model_transition_log;
DELETE FROM model_version;
"
```

(`$DATABASE_URL` is read from the backend pod's environment; the postgres CRD exposes it. If for any reason `psql` isn't available inside the backend pod, add `--rm` to a `kubectl run --image=postgres:16-alpine` or run from the operator's workstation against `lolday-postgres` service.)

- [ ] **Step 5: Backup DB**

```bash
kubectl exec -n lolday deploy/postgres -- pg_dump -U lolday lolday > backup-pre-handle-migration-$(date -I).sql
```

- [ ] **Step 6: Verify both PRs are merged into `main`**

```bash
gh pr view <PR-A-number> --json state -q .state  # MERGED
gh pr view <PR-B-number> --json state -q .state  # MERGED
```

- [ ] **Step 7: Deploy**

```bash
git checkout main && git pull
bash scripts/deploy.sh
```

The lolday Helm chart pulls the new image tags built by GHA on merge. Backend pod startup runs `alembic upgrade head` automatically.

- [ ] **Step 8: Verify migration applied**

```bash
kubectl exec -n lolday deploy/backend -- alembic current
```

Expected: head revision matches the new revision id from Task 4.

- [ ] **Step 9: Verify handles populated**

```bash
kubectl exec -n lolday deploy/backend -- psql "$DATABASE_URL" -c "SELECT count(*) FROM \"user\" WHERE handle IS NULL;"
```

Expected: `0`.

---

## Task 30: Post-deploy validation — 34 steps in 5 buckets

Execute the 34-step validation table from spec §4.4. Mark each row `[x]` as it passes; if any step fails, halt and triage.

### Bucket 1 — Schema sanity (4 steps)

- [ ] **1.1** `kubectl exec ... psql -c 'SELECT handle FROM "user" LIMIT 5;'` — all populated
- [ ] **1.2** `... -c '\dt'` shows `registered_model`, `model_visibility_log`, `model_owner_transfer_log`
- [ ] **1.3** `... -c '\d model_version'` shows `registered_model_id`, `visibility`; no `mlflow_name`
- [ ] **1.4** `... -c 'SELECT count(*) FROM model_version'` returns `0`

### Bucket 2 — elf-rf rebuild (12 steps)

- [ ] **2.1** UI onboard `elf-rf` (display "ELF RF"); existing repo URL OK
- [ ] **2.2** Build via UI; Harbor receives `harbor.lolday.svc/lolday/elf-rf:v3.0.1`
- [ ] **2.3** Train; ModelVersion v1 with `mlflow_name = "<your-handle>/elf-rf"` registered
- [ ] **2.4** Detail page shows v1 with Lock badge
- [ ] **2.5** Switch to test user; list `/models` doesn't show your elf-rf
- [ ] **2.6** Toggle v1 → public; Globe badge appears
- [ ] **2.7** Test user list now sees `<your-handle>/elf-rf`
- [ ] **2.8** Test user runs predict using v1 → succeeds
- [ ] **2.9** Toggle v1 → private; Lock badge
- [ ] **2.10** Test user retries predict → 422 "not accessible"
- [ ] **2.11** Test user trains against `elf-rf` detector → succeeds; new namespace `<test-handle>/elf-rf`
- [ ] **2.12** Both list pages show two entries: `<your-handle>/elf-rf` and `<test-handle>/elf-rf`

### Bucket 3 — elf-cnn parallel (12 steps)

- [ ] **3.1–3.12** Repeat Bucket 2 with `elf-cnn` (GPU resource profile)

### Bucket 4 — Description / tags / transfer (6 steps)

- [ ] **4.1** Edit description on `<your>/elf-rf` (markdown: heading + code block + list); renders correctly
- [ ] **4.2** Edit tags `{ "framework": "sklearn", "contract": "sample_csv" }`; pills display
- [ ] **4.3** Edit tags `{ "nested": { "x": "y" } }`; toast "Tags must be a flat JSON object"
- [ ] **4.4** Test user opens `<your>/elf-rf`; no Edit menu in kebab
- [ ] **4.5** Transfer `<your>/elf-cnn` to test user; URL updates to `/models/<test>/elf-cnn`; MLflow registered_model rename observed in MLflow UI
- [ ] **4.6** Try to transfer `<your>/elf-rf` to test user (test user already has elf-rf from 2.11) → 409

### Bucket 5 — Delete + cascade (4 steps)

- [ ] **5.1** Delete `<your>/elf-rf` (type-to-confirm); detail page navigates back to list
- [ ] **5.2** List shows only `<test>/elf-rf` (yours gone, test user's intact)
- [ ] **5.3** Test user deletes single version v2 of `<test>/elf-rf` (if multiple versions exist; otherwise create one); v2 gone, others intact
- [ ] **5.4** `kubectl exec ... psql -c 'SELECT * FROM model_owner_transfer_log;'` and `model_visibility_log;` — all bucket 2 / 4 / 5 actions logged with timestamps

**All 34 boxes ticked = phase complete. Notify operator + close GitHub milestone.**

---

## References

- **Spec:** `docs/superpowers/specs/2026-05-07-model-registry-namespace-and-visibility-design.md`
- **Branch convention:** `docs/conventions.md`
- **Pre-existing dataset visibility pattern:** `backend/app/models/dataset.py:28`, `backend/app/routers/datasets.py:30-40`
- **MLflow REST API 2.0:** <https://mlflow.org/docs/latest/rest-api.html>
- **HuggingFace Hub naming:** <https://huggingface.co/docs/hub/repositories-naming>
- **GitHub repo transfer (UX precedent):** <https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository>
