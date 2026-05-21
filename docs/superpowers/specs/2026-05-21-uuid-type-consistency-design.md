# Design: Standardise UUID column type on `sa.Uuid()`

Date: 2026-05-21
Owner: backend
Issue: <https://github.com/bolin8017/lolday/issues/530>

## 1. Problem

`backend/app/models/` uses two different SA UUID types across related
tables, which silently breaks foreign-key comparison on SQLite:

| Model                           | UUID columns                                                                                                                         | SA type                                             | SQLite DDL |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- | ---------- |
| `user.User.id`                  | 1                                                                                                                                    | `sqlalchemy.Uuid(as_uuid=True)` (generic)           | `CHAR(32)` |
| `detector.*`                    | `Detector.id/owner_id`, `DetectorBuild.id/detector_id/triggered_by_id`, `DetectorVersion.id/detector_id/uploaded_by_id` (6 explicit) | `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` | `UUID`     |
| `credential.Credential.user_id` | 1                                                                                                                                    | `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` | `UUID`     |

Every other model already uses `sa.Uuid()` (or the SA-2.0 implicit
mapping from `Mapped[uuid.UUID]`), matching the canonical
authentication design (`.claude/rules/backend.md` §Auth design: "the
phase 7.5 baseline migration was rewritten to use SQLAlchemy 2.0 native
`sa.Uuid()` directly").

### Concrete failure mode

SQLite's FK enforcement compares column values by their declared
column-type's stored form. `CHAR(32)` (User.id) and `UUID`
(Detector.owner_id) store the same Python UUID value with different
binary scalars; the FK check sees them as different and rejects:

```python
async with test_session_maker() as s:
    s.add(User(id=OID, ...))
    await s.flush()
    s.add(Detector(id=DID, ..., owner_id=OID))
    await s.commit()  # FOREIGN KEY constraint failed
```

The full integration suite passes today only because `setup_db`
teardown executes `PRAGMA foreign_keys=OFF` and the connection-pool
checkout reuses the OFF setting on the next test's checkout. The
upstream-mainstream pattern is the `checkout` event listener (not
`connect`), and ~14 reconciler tests across `test_reconciler.py` +
`test_reconciler_manifest.py` exploit the leak: they insert detectors
with `owner_id=uuid4()` without seeding a User row. Under a proper
`checkout` listener these tests would fail immediately under any
isolation level. The current order-dependent flakiness is documented
at `.claude/rules/testing.md` §4 (forbidden pattern).

## 2. Goal

The whole codebase declares UUID columns through a single SA type
(`sa.Uuid(as_uuid=True)`). The 13 reconciler/build tests stop relying
on a literal `uuid4()` FK reference and instead seed a real owner
User row through a shared fixture — even though FK enforcement is
still pool-leaky, the tests document the intent and become correct in
isolation.

## 3. Non-goals (this PR)

- **Flipping the conftest event listener from `connect` to `checkout`.**
  Doing so surfaces ~20 additional pre-existing FK-leak-dependent tests
  outside `tests/integration/reconciler/build*` (FIFO scheduler,
  summary projection, prediction summary, MLflow finalize, deps).
  Those require a deeper test-fixture refactor (Job FKs go three
  levels deep: owner ← detector ← detector_version) and are tracked in
  follow-up §10 tech debt #38. The current PR closes the canonical
  UUID-type drift documented in `.claude/rules/backend.md`; the
  event-listener swap is a logical follow-up once each FK-leaky test
  is reseeded.
- Postgres column-type change (`postgresql.UUID` and generic `sa.UUID`
  both compile to native `uuid` on Postgres — no DDL diff). The Alembic
  migration is a logical record of intent; it will be a no-op runtime
  on Postgres and a `recreate` on SQLite via `create_all`.
- Renaming any column. Column names stay.
- Switching to string-typed IDs.
- Touching MLflow-side UUID handling (different code path).

## 4. Design

### 4.1 Canonical UUID type

`sqlalchemy.Uuid(as_uuid=True)` — the SA 2.0 generic. On Postgres it
maps to the native `UUID` type; on SQLite it maps to `CHAR(32)`. One
type, one storage shape per backend, no dialect-specific imports.

### 4.2 Column-type migration

| File                                | Existing                                                 | Change to                                                                                                          |
| ----------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `app/models/detector.py`            | `from sqlalchemy.dialects.postgresql import JSONB, UUID` | `from sqlalchemy import Uuid`; drop `UUID` import; remove `JSONB`-shared import (keep separate import for `JSONB`) |
| `app/models/detector.py` (6 sites)  | `UUID(as_uuid=True)`                                     | `Uuid(as_uuid=True)`                                                                                               |
| `app/models/credential.py`          | `from sqlalchemy.dialects.postgresql import UUID`        | `from sqlalchemy import Uuid`                                                                                      |
| `app/models/credential.py` (1 site) | `UUID(as_uuid=True)`                                     | `Uuid(as_uuid=True)`                                                                                               |

Other models stay untouched — they already use `sa.Uuid()` (job_event)
or the SA-2.0 implicit `Mapped[uuid.UUID]` mapping (audit, dataset,
job, model_registry).

### 4.3 Alembic migration

Single new revision `<rev>_unify_uuid_column_types.py` with
`down_revision = "90125ce5ad35"`.

`upgrade()` issues `alembic.op.alter_column(..., type_=sa.Uuid())`
on the seven affected columns. The migration is a **no-op runtime on
Postgres** (both source `UUID` and target `UUID` types compile to the
same native `uuid` DDL); it exists to record the intent in the
alembic version log so the schema-at-head check in
`_assert_schema_at_head()` matches model state after the model edits.
`downgrade()` reverses to `postgresql.UUID(as_uuid=True)`.

### 4.4 Conftest event listener (deferred)

The conftest event listener stays at `"connect"` in this PR. The
mainstream-correct `"checkout"` swap surfaces ~20 FK-leak-dependent
tests outside the build reconciler path and is tracked separately
(see §3 Non-goals + §10 tech debt #38). The 13 reconciler/build
tests _would_ still pass under `checkout` because of the new owner
fixture; the deferral is about not blocking the unrelated FK-leaky
tests.

### 4.5 Owner fixture for reconciler tests

The 14 affected tests seed real users via a new fixture
`reconciler_owner` in `backend/tests/integration/reconciler/conftest.py`
that returns a freshly-inserted User row UUID. Tests change
`owner_id=uuid4()` → `owner_id=reconciler_owner` and `triggered_by_id=uuid4()`
→ `triggered_by_id=reconciler_owner` (one user is enough; the tests do
not exercise owner != triggered_by semantics).

## 5. Test plan

1. Reproducer from the issue body runs **green** instead of `IntegrityError`.
2. `cd backend && uv run pytest tests/integration/reconciler/ -q -p no:randomly --maxfail=1`
   passes deterministically across two consecutive runs (no leak).
3. `cd backend && uv run pytest -q` full suite passes.
4. `cd backend && uv run pytest -q -p no:randomly` (without shuffle) passes — the order-independence assumption holds.
5. `helm template charts/lolday` lints unchanged.
6. `alembic upgrade head` round-trip on a throwaway SQLite DB
   (`test_alembic.db`) succeeds.

## 6. Rollout

Single PR. Breaking changes are allowed (per project policy) but this
is a pure-internal schema change — no API / client / data-format
shifts. Existing Postgres data passes through the migration without
copy / rewrite.

## 7. Risks

- **Hidden FK violations surface in other tests.** Possible if a test
  outside `reconciler/` also seeds owner-less detectors and survived
  via the same pragma leak. Mitigation: run the full suite with
  shuffle (`-p randomly:randomly --randomly-dont-reset-seed`) several
  times; any post-fix failure is a separate-test fix, not a spec
  revisit.
- **Postgres DDL drift.** Verified once on a Postgres dev DB via
  heavy-tier migration test (`backend/tests/heavy/postgres/test_migrations_real_pg.py`)
  — already in CI's heavy tier.

## 8. Related

- `.claude/rules/backend.md` §Auth design — `sa.Uuid()` canonical
- `.claude/rules/testing.md` §4 — order-independent tests rule
- Issue #530 — full reproducer

## 9. Implementation plan

| Step | Description                                                                                                                                                                                                         |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S1   | Switch conftest event listener `connect` → `checkout`. Suite must turn red on the 14 reconciler tests; capture failure list.                                                                                        |
| S2   | Edit `app/models/detector.py` + `app/models/credential.py` to use `sa.Uuid(as_uuid=True)`.                                                                                                                          |
| S3   | Generate Alembic migration `<rev>_unify_uuid_column_types.py` via `alembic revision --autogenerate`; hand-edit if necessary (autogen may render empty on SQLite roundtrip — keep an explicit `alter_column` block). |
| S4   | Add `reconciler_owner` fixture in `backend/tests/integration/reconciler/conftest.py`.                                                                                                                               |
| S5   | Update the 14 affected tests to seed via fixture.                                                                                                                                                                   |
| S6   | Full suite + reverse-isolation single test pass.                                                                                                                                                                    |
