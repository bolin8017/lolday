# Drop fastapi-users User Vestige — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the fastapi-users vestige from the backend (`User` model, schema, auth, DB columns, and dependency) while preserving all observable application behaviour.

**Architecture:** Stop inheriting from `fastapi-users-db-sqlalchemy`'s `SQLAlchemyBaseUserTableUUID` and from `fastapi_users.schemas.BaseUser`; redefine `User` and `UserRead` from scratch using SQLAlchemy 2.0 / Pydantic v2 native primitives. Drop four columns (`hashed_password`, `is_active`, `is_superuser`, `is_verified`) via a new alembic migration, batch-mode for SQLite compatibility. Tighten the backend dep from `fastapi-users[sqlalchemy]` (39KB) down to `fastapi-users-db-sqlalchemy` (6.8KB) — the latter still feeds `generics.GUID()` to the phase 7.5 baseline migration.

**Tech Stack:** Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 async, Pydantic v2, Alembic, uv, pytest, aiosqlite, openapi-typescript.

**Spec:** `docs/superpowers/specs/2026-04-29-drop-hashed-password-design.md`

**Branch:** `chore/drop-hashed-password`

**Commit cadence:** 5 atomic commits, one per task group. Tests stay green at every commit boundary.

---

## File Structure (locked-in decomposition)

| File | Action | Purpose after PR |
|---|---|---|
| `backend/app/models/user.py` | Rewrite | `User` ORM class without fastapi-users base class; only application-domain columns |
| `backend/app/schemas/user.py` | Rewrite | `UserRead` / `UserSelfUpdate` as native Pydantic v2 BaseModels |
| `backend/app/auth/cf_access.py` | Edit (small) | Drop 3 vestige kwargs from `User(...)` constructor; remove `_sso_sentinel_password()` helper + `import secrets` |
| `backend/migrations/versions/<rev>_drop_fastapi_users_user_columns.py` | Create | Single migration: drop 4 columns via `op.batch_alter_table` |
| `backend/pyproject.toml` | Edit (1 line) | Replace dep `fastapi-users[sqlalchemy]` → `fastapi-users-db-sqlalchemy` with comment |
| `backend/uv.lock` | Regenerate | Result of `uv lock` |
| `backend/tests/conftest.py` | Edit | `_make_user` signature drops `is_superuser`; `User(...)` drops 4 kwargs; `auth_client_admin` caller drops `is_superuser=True` |
| `backend/tests/test_*.py` (10 files) | Edit | Drop 4 kwargs from User constructions and raw SQL |
| `frontend/src/api/schema.gen.ts` | Regenerate | Auto-removes 3 boolean keys from `UserRead` |
| `docs/architecture.md` | Edit (one entry) | §9 #7 marked resolved |
| `.claude/rules/backend.md` | Edit (one paragraph) | Auth design bullet rewritten |

---

## Task 1: Strip optional fastapi-users kwargs from test fixtures

**Why first:** `is_active=True`, `is_superuser=*`, `is_verified=True` all have model-level defaults inherited from `SQLAlchemyBaseUserTable` (`default=True/False/False`). Removing them now is a no-op against the current schema — tests stay green. This isolates the 11-file mechanical change from the structural refactor.

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_internal_events.py`
- Modify: `backend/tests/test_reconciler_events.py`
- Modify: `backend/tests/test_service_token_notify.py`
- Modify: `backend/tests/test_jobs_events_endpoint.py`
- Modify: `backend/tests/test_jobs_events_websocket.py`
- Modify: `backend/tests/test_services_events_tail.py`
- Modify: `backend/tests/test_models_job_event.py`
- Modify: `backend/tests/test_user_discord_id.py`
- Modify: `backend/tests/test_role_enum_roundtrip.py` (ORM inserts only — raw SQL stays for now)

**NOT modified:**
- `backend/tests/test_admin.py` — sends `is_superuser` in JSON body to verify `extra="forbid"` rejects it. After everything ships, that key is still unknown to the schema and still 422s. No change.
- `backend/tests/test_migrations_phase12.py` — runs alembic to phase 12.x revisions only (before our new migration). Those revisions still have the columns. Raw SQL stays.

- [ ] **Step 1.1: Run baseline pytest to confirm green starting state**

```bash
cd backend && uv run pytest -q 2>&1 | tail -20
```

Expected: all tests pass. If anything fails before our changes, stop and investigate — these failures are out of scope.

- [ ] **Step 1.2: Update `backend/tests/conftest.py` — `_make_user` signature**

In `backend/tests/conftest.py`, change:

```python
async def _make_user(
    email: str,
    role: Role = Role.USER,
    is_superuser: bool = False,
) -> User:
```

to:

```python
async def _make_user(
    email: str,
    role: Role = Role.USER,
) -> User:
```

And in the same function, change the `User(...)` constructor from:

```python
        user = User(
            email=email,
            hashed_password="!testing-only!",
            role=role,
            display_name=email.split("@", 1)[0],
            is_active=True,
            is_superuser=is_superuser,
            is_verified=True,
        )
```

to (keep `hashed_password=` for now — column is still required NOT NULL with no default):

```python
        user = User(
            email=email,
            hashed_password="!testing-only!",
            role=role,
            display_name=email.split("@", 1)[0],
        )
```

- [ ] **Step 1.3: Update `backend/tests/conftest.py` — `auth_client_admin` caller**

Around line 128, change:

```python
    await _make_user("adm@example.dev", role=Role.ADMIN, is_superuser=True)
```

to:

```python
    await _make_user("adm@example.dev", role=Role.ADMIN)
```

- [ ] **Step 1.4: Strip 3 boolean kwargs from feature test files**

For each of the 8 files below, remove the lines `is_active=True,`, `is_superuser=...,` (any value), and `is_verified=True,` from every `User(...)` constructor call. Keep `hashed_password="..."` (any value) — that line stays. Use grep to spot-check:

```bash
cd backend && grep -n "is_active\|is_superuser\|is_verified" tests/test_internal_events.py tests/test_reconciler_events.py tests/test_service_token_notify.py tests/test_jobs_events_endpoint.py tests/test_jobs_events_websocket.py tests/test_services_events_tail.py tests/test_models_job_event.py tests/test_user_discord_id.py
```

Expected: list of every line that needs to change.

For each match, edit the file to remove just those 3 kwargs. Example:

```python
# before
user = User(
    email="x@example.dev",
    hashed_password="x",
    role=Role.USER,
    is_verified=True,
)

# after
user = User(
    email="x@example.dev",
    hashed_password="x",
    role=Role.USER,
)
```

For `backend/tests/test_user_discord_id.py:18`, change:

```python
await _make_user(email, role=Role.ADMIN, is_superuser=True)
```

to:

```python
await _make_user(email, role=Role.ADMIN)
```

- [ ] **Step 1.5: Update `backend/tests/test_role_enum_roundtrip.py` — ORM inserts only**

In the two `User(...)` constructor calls (around lines 73-83 and 116-127), remove `is_active=True,`, `is_superuser=False,`, and `is_verified=True,`. Keep `hashed_password="!testing-only!",`. Example transform:

```python
# before
session.add(
    User(
        id=user_id,
        email=f"role-{role.name.lower()}@example.dev",
        hashed_password="!testing-only!",
        role=role,
        display_name=role.name,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
)

# after
session.add(
    User(
        id=user_id,
        email=f"role-{role.name.lower()}@example.dev",
        hashed_password="!testing-only!",
        role=role,
        display_name=role.name,
    )
)
```

**Do NOT touch the raw SQL `INSERT` block (around lines 159-172) yet.** That raw SQL inserts directly into the DB which still has the NOT NULL columns at this commit. We'll fix it in Task 4.

- [ ] **Step 1.6: Run pytest to confirm still green**

```bash
cd backend && uv run pytest -q 2>&1 | tail -20
```

Expected: same number of tests pass as in Step 1.1. If any fail with `TypeError: User got an unexpected keyword argument`, you missed a kwarg — re-check the file the failing test is in.

- [ ] **Step 1.7: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add backend/tests/conftest.py backend/tests/test_*.py
git status  # verify only test files staged
git commit -m "$(cat <<'EOF'
test(backend): strip optional fastapi_users kwargs from test fixtures

is_active / is_superuser / is_verified all have model-level defaults
(True / False / False) inherited from SQLAlchemyBaseUserTable. Stripping
them now is a no-op against the current schema; isolates the mechanical
test-fixture cleanup from the upcoming structural refactor.

hashed_password is required NOT NULL with no default — kept for now;
removed in the next commit alongside the schema/migration changes.

test_role_enum_roundtrip.py raw SQL block (lines ~159-172) intentionally
NOT touched — that still runs against a schema with the columns present.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Generate alembic migration shell

**Files:**
- Create: `backend/migrations/versions/<rev>_drop_fastapi_users_user_columns.py`

- [ ] **Step 2.1: Verify current alembic head**

```bash
cd backend && uv run alembic heads
```

Expected output: `f37230063a20 (head)`. If the head is different, update the `down_revision` in Step 2.3 accordingly.

- [ ] **Step 2.2: Generate the migration file**

```bash
cd backend && uv run alembic revision -m "drop fastapi_users user columns"
```

Note: do **not** use `--autogenerate`. Autogenerate compares model-to-DB; we'll hand-write the upgrade/downgrade. The generated file lives under `backend/migrations/versions/` with an alembic-assigned `<rev>_drop_fastapi_users_user_columns.py` filename.

Capture the generated revision ID (printed in the alembic output) — you'll need it to verify the file.

- [ ] **Step 2.3: Replace the generated file body**

Open the new file and replace its contents with (keep the auto-generated `revision: str` and `Create Date` values):

```python
"""drop fastapi-users vestige columns from user table

Phase 10 migrated to Cloudflare Access SSO. The four columns inherited from
fastapi-users-db-sqlalchemy (hashed_password, is_active, is_superuser,
is_verified) have been written-but-never-read since. Resolves
docs/architecture.md §9 #7.

Revision ID: <keep what alembic generated>
Revises: f37230063a20
Create Date: <keep what alembic generated>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Keep the revision ID alembic generated above; do not hand-edit it.
revision: str = "<keep>"
down_revision: Union[str, Sequence[str], None] = "f37230063a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("is_active")
        batch_op.drop_column("is_superuser")
        batch_op.drop_column("is_verified")


def downgrade() -> None:
    """Local-dev rollback only — repo policy forbids prod downgrades
    (.claude/rules/alembic-migrations.md). Columns restored as nullable;
    original constant values (hashed_password sentinel, is_active=true,
    is_verified=true, is_superuser=false) are not backfilled."""
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("hashed_password", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_superuser", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_verified", sa.Boolean(), nullable=True))
```

- [ ] **Step 2.4: Verify alembic recognises the new head**

```bash
cd backend && uv run alembic heads
```

Expected: a single head matching the new revision ID (no longer `f37230063a20`). If you see two heads, your `down_revision` is wrong — fix it to point at `f37230063a20`.

---

## Task 3: Refactor model, schema, and cf_access

**Files:**
- Rewrite: `backend/app/models/user.py`
- Rewrite: `backend/app/schemas/user.py`
- Edit: `backend/app/auth/cf_access.py`

- [ ] **Step 3.1: Replace `backend/app/models/user.py`**

Replace the entire file contents with:

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"
    # Machine principal — set on rows created from a Cloudflare Access
    # service-token JWT (synthesised email ``service-<cn>@cf-access.local``).
    # Discord notification policy keys off ``Role.SERVICE_TOKEN`` so the
    # rule survives the operator editing a row's email by hand.
    SERVICE_TOKEN = "service_token"


# cf_access.py synthesises ``service-<common_name>@cf-access.local`` for
# JWTs that carry only ``common_name`` (CF Access service-token principals).
SERVICE_TOKEN_EMAIL_DOMAIN = "@cf-access.local"
SERVICE_TOKEN_DISPLAY_NAME = "Internal service token"


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(length=320), unique=True, index=True, nullable=False,
    )
    role: Mapped[Role] = mapped_column(
        SAEnum(
            Role,
            name="role_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=Role.USER,
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    discord_user_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def is_service_token(self) -> bool:
        """True for CF Access service-token principals.

        Backed by ``role``, not by an email-suffix probe — survives an
        admin editing the email field, surfaces in /admin/users as a
        normal column, and is indexable via the existing ``role_enum``.
        """
        return self.role == Role.SERVICE_TOKEN
```

- [ ] **Step 3.2: Replace `backend/app/schemas/user.py`**

Replace the entire file contents with:

```python
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import Role


# Discord snowflakes are 64-bit IDs serialised as decimal strings, today
# 17–19 digits with legacy and future IDs bracketing 15–20.
_DISCORD_ID_RE = re.compile(r"^\d{15,20}$")


def _validate_discord_user_id(v):
    """Allow None, coerce empty string → None, else require 15–20 digits."""
    if v is None or v == "":
        return None
    if not _DISCORD_ID_RE.match(v):
        raise ValueError(
            "discord_user_id must be 15–20 digits (copy from Discord "
            "with Developer Mode enabled → right-click → Copy User ID)"
        )
    return v


class UserRead(BaseModel):
    # email stays as plain ``str``: Cloudflare Access service tokens
    # synthesize ``service-<name>@cf-access.local``, and Pydantic's
    # ``EmailStr`` rejects ``.local`` (RFC 6761 reserved TLD). Email
    # format is validated on user creation; the response shape doesn't
    # need to re-validate.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    display_name: str | None = None
    discord_user_id: str | None = None
    created_at: datetime | None = None


class UserSelfUpdate(BaseModel):
    """Body accepted by ``PATCH /users/me`` — only self-mutable fields.

    ``extra="forbid"`` means sending ``role``, ``email``, etc. returns 422
    rather than silently dropping them. This is the sole line between a
    regular user and privilege escalation through ``/users/me``; see
    ``tests/test_user_discord_id.py::test_patch_users_me_rejects_role_smuggling``.
    """
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    discord_user_id: str | None = None

    _validate_discord_self = field_validator("discord_user_id", mode="before")(
        _validate_discord_user_id
    )
```

- [ ] **Step 3.3: Edit `backend/app/auth/cf_access.py` — drop sentinel helper**

Remove `_sso_sentinel_password()` function (lines 62–65 in the current file):

```python
# DELETE these lines:
def _sso_sentinel_password() -> str:
    # SSO users never log in via password. Store a syntactically-invalid
    # hash so any accidental verify() call fails closed.
    return f"!sso_only!{secrets.token_urlsafe(16)}"
```

Also remove `import secrets` near the top of the file — no other use site.

- [ ] **Step 3.4: Edit `backend/app/auth/cf_access.py` — drop vestige kwargs from User()**

Find the `User(...)` constructor inside `get_or_create_user_by_email()` (around line 121–128). Change from:

```python
    user = User(
        email=email,
        hashed_password=_sso_sentinel_password(),
        role=initial_role,
        display_name=_default_display_name_for(email),
        is_active=True,
        is_verified=True,
    )
```

to:

```python
    user = User(
        email=email,
        role=initial_role,
        display_name=_default_display_name_for(email),
    )
```

- [ ] **Step 3.5: Static check — no fastapi_users imports remain in app code**

```bash
cd backend && grep -rn "fastapi_users\|from fastapi_users" app/ --include="*.py"
```

Expected: no output (zero matches). If anything is left, remove it.

---

## Task 4: Strip remaining `hashed_password=` from tests + raw SQL

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_internal_events.py`, `test_reconciler_events.py`, `test_service_token_notify.py`, `test_jobs_events_endpoint.py`, `test_jobs_events_websocket.py`, `test_services_events_tail.py`, `test_models_job_event.py`
- Modify: `backend/tests/test_role_enum_roundtrip.py` (both ORM and raw SQL)

- [ ] **Step 4.1: Locate every remaining `hashed_password=` in tests**

```bash
cd backend && grep -rn "hashed_password" tests/
```

Expected output: every test file that still has `hashed_password="..."` in a `User(...)` constructor or raw SQL `INSERT`.

- [ ] **Step 4.2: Strip `hashed_password=` from `backend/tests/conftest.py`**

Inside `_make_user`, change:

```python
        user = User(
            email=email,
            hashed_password="!testing-only!",
            role=role,
            display_name=email.split("@", 1)[0],
        )
```

to:

```python
        user = User(
            email=email,
            role=role,
            display_name=email.split("@", 1)[0],
        )
```

- [ ] **Step 4.3: Strip `hashed_password=` from each affected test file**

For each `User(...)` constructor in the 7 feature test files, remove the `hashed_password="..."` line. Example transform:

```python
# before
user = User(
    email="x@example.dev",
    hashed_password="x",
    role=Role.USER,
)

# after
user = User(
    email="x@example.dev",
    role=Role.USER,
)
```

- [ ] **Step 4.4: Strip kwargs + helper from `backend/tests/test_role_enum_roundtrip.py`**

Two ORM `User(...)` calls (around lines 73-83 and 116-127): remove `hashed_password="!testing-only!",` line.

The raw SQL block (around lines 159-172) — change:

```python
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'INSERT INTO "user" '
                "(id, email, hashed_password, role, display_name, "
                "is_active, is_verified, is_superuser) "
                "VALUES (:id, :email, '!', :role, :dn, 1, 1, 0)"
            ),
            {
                "id": str(user_id),
                "email": "service-test@cf-access.local",
                "role": "service_token",
                "dn": "Internal service token",
            },
        )
```

to:

```python
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'INSERT INTO "user" '
                "(id, email, role, display_name) "
                "VALUES (:id, :email, :role, :dn)"
            ),
            {
                "id": str(user_id),
                "email": "service-test@cf-access.local",
                "role": "service_token",
                "dn": "Internal service token",
            },
        )
```

- [ ] **Step 4.5: Verify zero `hashed_password` references in `backend/`**

```bash
cd backend && grep -rn "hashed_password" --include="*.py" .
```

Expected: no output. Any remaining match must be hunted down (likely a comment somewhere — comment must also be updated).

---

## Task 5: Verify migration + tests, then commit refactor

- [ ] **Step 5.1: Wipe stale test DB, run alembic upgrade head**

```bash
cd backend && rm -f test.db && uv run alembic upgrade head 2>&1 | tail -20
```

Expected: alembic runs every migration from baseline through our new drop migration without error. The final line should be the new revision ID (the one alembic generated in Step 2.2).

- [ ] **Step 5.2: Run full pytest**

```bash
cd backend && uv run pytest 2>&1 | tail -30
```

Expected: all tests pass. If `test_role_enum_roundtrip.py` fails with `IntegrityError: NOT NULL constraint failed: user.hashed_password`, your migration didn't run cleanly against the test DB — re-check Step 5.1's output. If a generic test fails with `TypeError: User got an unexpected keyword argument 'hashed_password'`, you missed a test file in Step 4.3 — grep again.

- [ ] **Step 5.3: Sanity — confirm no fastapi_users imports remain in app/**

```bash
cd backend && grep -rn "fastapi_users" app/ --include="*.py" && \
  echo "FAIL: matches above" || echo "OK: zero matches"
```

Expected: `OK: zero matches`. The pytest run in Step 5.2 already proves the schema is correct (the migration tests insert into the post-drop schema); a separate column-list check is redundant.

- [ ] **Step 5.4: Commit refactor**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add backend/app/models/user.py backend/app/schemas/user.py backend/app/auth/cf_access.py
git add backend/migrations/versions/*_drop_fastapi_users_user_columns.py
git add backend/tests/conftest.py backend/tests/test_*.py
git status  # verify file list
git commit -m "$(cat <<'EOF'
refactor(backend): drop fastapi_users User base class and vestige columns

User model and UserRead schema no longer inherit from fastapi-users base
classes. SQLAlchemy 2.0 native Uuid replaces the third-party GUID
TypeDecorator. New alembic migration drops four columns
(hashed_password, is_active, is_superuser, is_verified) — written-but-
never-read since Phase 10 SSO migration. cf_access user creation drops
three vestige kwargs and the _sso_sentinel_password helper.

API contract change: UserRead JSON drops `is_active`, `is_superuser`,
`is_verified` keys (always constant true / false / true in production;
no client elements read them). Frontend regen handled in a follow-up
commit.

Resolves docs/architecture.md §9 #7 fastapi-users vestige.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Tighten backend dep

**Files:**
- Modify: `backend/pyproject.toml`
- Regenerate: `backend/uv.lock`

- [ ] **Step 6.1: Edit `backend/pyproject.toml`**

Find the line `"fastapi-users[sqlalchemy]>=14.0.0",` (around line 9) in the `dependencies = [...]` list. Replace it with:

```toml
    # phase 7.5 baseline migration imports fastapi_users_db_sqlalchemy.generics.GUID;
    # tests re-run that migration on aiosqlite. Dep stays as a transitive baseline
    # requirement only — runtime app code has zero fastapi_users imports.
    "fastapi-users-db-sqlalchemy>=7,<8",
```

- [ ] **Step 6.2: Regenerate the lockfile**

```bash
cd backend && uv lock
```

Expected: `uv.lock` updates. The `fastapi-users` package (15.0.5) entry is removed; `fastapi-users-db-sqlalchemy` (7.x) entry stays.

- [ ] **Step 6.3: Sync the dev environment**

```bash
cd backend && uv sync --dev
```

Expected: uv removes `fastapi-users` from the venv and keeps `fastapi-users-db-sqlalchemy`.

- [ ] **Step 6.4: Verify `fastapi_users` is gone, but `fastapi_users_db_sqlalchemy` remains**

```bash
cd backend && uv run python -c "import fastapi_users" 2>&1 | grep -q "No module" \
  && echo "OK: fastapi_users gone" || echo "FAIL: fastapi_users still present"
```

Expected: `OK: fastapi_users gone`.

```bash
cd backend && uv run python -c "from fastapi_users_db_sqlalchemy.generics import GUID; print('OK:', GUID)"
```

Expected: `OK: <class 'fastapi_users_db_sqlalchemy.generics.GUID'>`.

- [ ] **Step 6.5: Re-run pytest**

```bash
cd backend && uv run pytest 2>&1 | tail -10
```

Expected: all tests pass (same count as Step 5.2). If anything regresses, the most likely cause is a stray `from fastapi_users import ...` somewhere in `backend/` that the previous grep missed — re-grep including `tests/`:

```bash
cd backend && grep -rn "from fastapi_users\|import fastapi_users" --include="*.py" .
```

Note: `import fastapi_users_db_sqlalchemy` in `backend/migrations/versions/d3f179666394_phase7_5_baseline.py` is **expected** to remain — that's the baseline migration. Do not edit it.

- [ ] **Step 6.6: Commit deps**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add backend/pyproject.toml backend/uv.lock
git status  # verify only those two files staged
git commit -m "$(cat <<'EOF'
chore(deps): replace fastapi-users with fastapi-users-db-sqlalchemy

Runtime app code no longer imports fastapi_users after the previous
commit. Tighten the dep to the only sub-package still needed: the
6.8KB fastapi-users-db-sqlalchemy, used by phase 7.5 baseline
migration's generics.GUID() type. The 39KB fastapi-users package
(routers / auth backends / UserManager / schemas) is gone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Regenerate frontend API types

**Files:**
- Regenerate: `frontend/src/api/schema.gen.ts`

- [ ] **Step 7.1: Generate fresh OpenAPI document from the in-process app**

The existing `frontend/scripts/gen-api-types.sh` expects a running backend at `http://localhost:8000/openapi.json`. Skip the live server — generate the spec offline by importing the FastAPI app:

```bash
cd backend && ENVIRONMENT=test FERNET_KEY=ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg= \
  uv run python -c "
import json
from app.main import app
print(json.dumps(app.openapi()))
" > /tmp/lolday-openapi.json
```

Expected: `/tmp/lolday-openapi.json` exists, ~50–200KB JSON with valid OpenAPI 3 structure. If `app.main` import fails with a config error, ensure the env vars above are exported (Pydantic Settings's production validator is opt-in via `ENVIRONMENT=production`).

- [ ] **Step 7.2: Run openapi-typescript against the generated file**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm exec openapi-typescript /tmp/lolday-openapi.json -o src/api/schema.gen.ts
```

Expected: `frontend/src/api/schema.gen.ts` is rewritten. The console prints something like `🚀 Generated /path/to/schema.gen.ts in Xms`.

- [ ] **Step 7.3: Verify the diff**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git diff frontend/src/api/schema.gen.ts | head -40
```

Expected: the `UserRead` type loses three keys:

```diff
 UserRead: {
     /**
      * Id
      * Format: uuid
      */
     id: string;
     /** Email */
     email: string;
-    /**
-     * Is Active
-     * @default true
-     */
-    is_active: boolean;
-    /**
-     * Is Superuser
-     * @default false
-     */
-    is_superuser: boolean;
-    /**
-     * Is Verified
-     * @default false
-     */
-    is_verified: boolean;
     role: components["schemas"]["Role"];
     ...
 };
```

If the diff shows other unexpected changes (e.g. unrelated endpoint reordering), inspect them — they may be auto-generated noise that's fine to commit, but worth noting in the commit message body.

- [ ] **Step 7.4: Frontend lint / typecheck / test**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm typecheck 2>&1 | tail -10
pnpm lint 2>&1 | tail -10
pnpm test 2>&1 | tail -10
```

Expected: all three commands exit 0. If `pnpm typecheck` fails because some hand-written component reads `user.is_active`, that contradicts the spec's pre-flight grep — investigate the failing line and either delete the dead reference or escalate (the spec says zero hand-written references exist).

- [ ] **Step 7.5: Commit frontend regen**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/schema.gen.ts
git commit -m "$(cat <<'EOF'
chore(frontend): regenerate api types after fastapi-users removal

UserRead drops is_active / is_superuser / is_verified booleans, mirroring
the backend schema change. No hand-written frontend code referenced these
fields — verified by grep before the backend refactor.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Mark vestige resolved in docs + helm sanity

**Files:**
- Modify: `docs/architecture.md`
- Modify: `.claude/rules/backend.md`

- [ ] **Step 8.1: Update `docs/architecture.md` §9 #7**

Find item 7 in §9 ("Known tech debt"), currently:

```markdown
7. **fastapi-users vestige** — `User.hashed_password` column still present but unused since Phase 10 SSO migration.
```

Replace with:

```markdown
7. ~~**fastapi-users vestige**~~ — resolved 2026-04-29 in `chore/drop-hashed-password`: User model + schema no longer inherit from fastapi-users base classes; `hashed_password` was dropped along with three other unused booleans (`is_active` / `is_superuser` / `is_verified`). The dep was tightened from `fastapi-users[sqlalchemy]` to `fastapi-users-db-sqlalchemy` (the latter still feeds `generics.GUID()` to the phase 7.5 baseline migration).
```

- [ ] **Step 8.2: Update `.claude/rules/backend.md` Auth design**

Find the first bullet in the "Auth design" section (around line 32):

```markdown
- fastapi-users is a vestigial dependency. The password-flow routers, transports, and UserManager were stripped in Phase 10. The `User` model still inherits `SQLAlchemyBaseUserTableUUID`, but `hashed_password` is unused (tracked as tech debt).
```

Replace with:

```markdown
- Authentication is exclusively via `cf_access_user`. The `fastapi-users` package itself is not installed; only `fastapi-users-db-sqlalchemy` remains as a transitive dep for the phase 7.5 baseline migration's `generics.GUID` type. Do not add new auth backends, do not reintroduce `fastapi_users` imports.
```

- [ ] **Step 8.3: Helm lint sanity**

```bash
cd /home/bolin8017/Documents/repositories/lolday
helm lint charts/lolday 2>&1 | tail -20
```

Expected: no new errors / warnings versus the pre-PR state. The chart isn't directly affected by this PR, but the spec lists this as a sanity check.

- [ ] **Step 8.4: Commit docs**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add docs/architecture.md .claude/rules/backend.md
git commit -m "$(cat <<'EOF'
docs: mark fastapi-users vestige as resolved

architecture.md §9 #7 strikethrough + resolved note. backend.md auth
design bullet rewritten to reflect the current state: zero fastapi_users
runtime imports; only fastapi-users-db-sqlalchemy remains as a
transitive dep for the phase 7.5 baseline migration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final acceptance checks

- [ ] **Step 9.1: Acceptance — all greps clean**

```bash
cd /home/bolin8017/Documents/repositories/lolday
echo "--- hashed_password / SQLAlchemyBaseUserTableUUID / fastapi_users in backend/app ---"
git grep "hashed_password\|SQLAlchemyBaseUserTableUUID\|from fastapi_users" backend/app && \
  echo "FAIL: matches above" || echo "OK: zero matches"
```

Expected: `OK: zero matches`.

- [ ] **Step 9.2: Acceptance — fresh DB upgrade head**

```bash
cd backend && rm -f test.db && uv run alembic upgrade head 2>&1 | tail -5
```

Expected: alembic runs all migrations through to the new drop migration cleanly.

- [ ] **Step 9.3: Acceptance — full backend test suite**

```bash
cd backend && uv run pytest 2>&1 | tail -10
```

Expected: same number of tests pass as in Step 1.1 (baseline). Zero failures.

- [ ] **Step 9.4: Acceptance — frontend gates**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm typecheck && pnpm lint && pnpm test 2>&1 | tail -5
```

Expected: all three pass.

- [ ] **Step 9.5: Acceptance — dep confirmation**

```bash
cd backend && uv run python -c "import fastapi_users" 2>&1 | grep -q "No module" \
  && echo "OK" || echo "FAIL"
cd backend && uv run python -c "from fastapi_users_db_sqlalchemy.generics import GUID; print('OK')"
```

Both expected: `OK`.

- [ ] **Step 9.6: Acceptance — git log readability**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git log --oneline main..HEAD
```

Expected: 5 commits on this branch, all conforming to Conventional Commits format. If the branch is `chore/drop-hashed-password`, the diff against `main` should match the file list in the "File Structure" table at the top of this plan.

---

## Open questions / hand-offs

- Frontend `pnpm playwright test` — not part of plan verification; spec leaves it for reviewer discretion. Implementer can run it locally if desired but it requires backend running with a real CF Access JWT, which is non-trivial in a dev worktree.
- Server30 dev cluster smoke test — left to `superpowers:finishing-a-development-branch` phase. The new alembic migration will be picked up by the `templates/alembic-upgrade-hook.yaml` pre-upgrade Job on the next deploy.
