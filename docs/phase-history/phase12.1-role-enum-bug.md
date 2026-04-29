# Phase 12.1 — `role_enum` case-inconsistency bug

**Discovered:** 2026-04-28 during Phase 13a deploy verification.
**Introduced:** Phase 12 (commit `b77b112`, "service-token notify skip").
**Severity:** Medium — only affects automation/CI authenticated via Cloudflare Access service token. End-user OAuth login is unaffected.

---

## Symptom

Any request hitting an endpoint that loads a `User` row created from a CF Access service-token JWT returns HTTP 500 with backend traceback:

```
sqlalchemy.sql.sqltypes._object_value_for_elem
LookupError: 'service_token' is not among the defined enum values.
Enum name: role_enum.
Possible values: ADMIN, DEVELOPER, USER, SERVICE_TOKEN
```

Confirmed reproduction: `curl -H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..." https://lolday.connlabai.com/api/v1/users/me` → 500.

User-facing impact: the `phase11d-chart-verify`, `phase11e-full-flow`, and (newly authored) `phase13a-verify` Playwright specs all fail with "Session not established" because the SPA can't load `/users/me`.

---

## Root cause

`backend/app/models/user.py`:

```python
class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"
    SERVICE_TOKEN = "service_token"   # phase 12

class User(Base):
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum"), default=Role.USER, nullable=False
    )
```

`SAEnum(Role, name="role_enum")` defaults to **storing enum NAMES** (`ADMIN`, `DEVELOPER`, `USER`, `SERVICE_TOKEN` — uppercase) in the PostgreSQL enum type.

Phase 12's migration that added the new enum value used the lowercase **VALUE** (`service_token`) instead of the NAME (`SERVICE_TOKEN`). Resulting DB state:

```sql
SELECT enum_range(NULL::role_enum);
 -> {ADMIN, DEVELOPER, USER, service_token}
```

When the backend creates a service-token user, the row stores `role='service_token'` (lowercase, the VALUE). When SQLAlchemy reads the row, it looks up `'service_token'` against the Role enum's NAMES and fails because no `service_token` NAME exists (only `SERVICE_TOKEN`).

Existing pre-phase-12 user rows (`bolin8017@gmail.com|ADMIN`, `tammy60327@gmail.com|USER`) use the uppercase NAME and read back correctly.

---

## Fix options

### Option A — uppercase the enum value (preferred)

Migration:

```python
def upgrade():
    op.execute("COMMIT")
    op.execute("ALTER TYPE role_enum RENAME VALUE 'service_token' TO 'SERVICE_TOKEN'")
    op.execute("UPDATE \"user\" SET role = 'SERVICE_TOKEN' WHERE role = 'service_token'")
```

Pros: minimal — keeps existing semantic, fixes the broken value.
Cons: `ALTER TYPE ... RENAME VALUE` requires PostgreSQL ≥ 10 (we're on 16+, fine).

Note: any cached SQLAlchemy connection may have stale enum metadata. After the migration, a backend rolling restart is required.

### Option B — switch SAEnum to value-based storage

Change all `Role` enum cases to be lowercase consistently:

```python
class User(Base):
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum", values_callable=lambda x: [m.value for m in x]),
        default=Role.USER,
        nullable=False,
    )
```

Plus a migration to rename the existing values to their lowercase forms (`ADMIN` → `admin`, etc.).

Pros: more conventional (SQLAlchemy's `values_callable` is the recommended pattern for `str`-mixin enums).
Cons: bigger blast radius — every existing user row gets re-written; risk of forgetting a downstream consumer.

---

## Recommendation

> ⚠️ **Superseded — see "Resolution" section below.** We adopted Option B
> after auditing the rest of the codebase's `SAEnum` usage; the rationale
> is in the Resolution section.

Go with **Option A**: smaller migration, identical semantics, fixes the immediate breakage.

If we adopt B in the future, do it as a separate dedicated cleanup phase with explicit data migration tests.

---

## Test that should pass after fix

```bash
source .lolday-secrets.env  # CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET live here as of 2026-04-29
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  https://lolday.connlabai.com/api/v1/users/me
# expected: 200 (was 500)
```

And the existing Playwright specs (`phase11d-chart-verify`, `phase11e-full-flow`, `phase13a-verify`) should run end-to-end with `PHASE*_VERIFY=1`.

---

## Out of scope here

- Doesn't touch the role enum's interaction with RBAC checks (`require_role`, `require_detector_access`) — those compare against `Role.ADMIN` / `Role.DEVELOPER` directly via the Python enum so they don't care about the storage form.
- Doesn't add a new role or change existing role semantics.

---

## Suggested PR scope

> **Historical — superseded; see "Resolution" section below.** The actual
> PR scope diverged because we picked Option B over Option A.

- Branch: `phase12.1-role-enum-fix`
- Files:
  - `backend/migrations/versions/<hash>_phase12_1_role_enum_uppercase.py` — Option A migration
  - `backend/tests/test_role_enum_roundtrip.py` — small regression test (insert+read SERVICE_TOKEN role)
- Deploy: rolling restart of backend after migration applies.
- After deploy: re-run `pnpm playwright test phase13a-verify.spec.ts` to confirm.

---

## Resolution (2026-04-28) — adopted Option B

After auditing the rest of the backend models we found that `role_enum` is
the **only** ``SAEnum`` in the codebase **without** ``values_callable``:

| File | Enum | ``values_callable``? |
|------|------|----------------------|
| ``dataset.py`` ``DatasetVisibility`` | ✅ |
| ``model_registry.py`` ``ModelVersionStage`` | ✅ |
| ``credential.py`` ``GitProvider`` | ✅ |
| ``detector.py`` ``DetectorVersionStatus, DetectorBuildStatus`` | ✅ |
| ``job.py`` ``JobType, JobStatus, ResourceProfile`` | ✅ |
| **``user.py`` ``Role``** | ❌ |

Phase 12.2's migration was written by someone who (correctly) assumed the
codebase-wide convention applied to ``role_enum`` and added the new value
as the lowercase ``service_token``. The mismatch between that assumption
and ``user.py`` 's NAME-based ``SAEnum`` was the actual root cause.

Option A (rename only the new value back to upper) would patch the symptom
while leaving ``role_enum`` as a permanent codebase exception. The next
person to add a role would fall into the same trap.

We adopted **Option B**: align ``role_enum`` to the ``values_callable``
convention. The migration ``a4b8e7c91d52_phase12_3_role_enum_lowercase``
renames the three uppercase NAMEs (``ADMIN``, ``DEVELOPER``, ``USER``)
to their lowercase VALUEs in PostgreSQL via ``ALTER TYPE ... RENAME VALUE``;
``service_token`` was already lowercase so it needs no rename. The model
``backend/app/models/user.py`` adds
``values_callable=lambda x: [e.value for e in x]`` to the ``role`` column.
After this pair, every enum in the codebase is value-stored.

Verification (post-deploy 2026-04-28):

* ``backend/tests/test_role_enum_roundtrip.py`` — 5 new tests covering
  every Role member's ORM round-trip + the raw ``'service_token'``
  read path (failed before fix with the prod traceback's ``LookupError``).
* ``backend/tests`` — 432 total pass, no regressions.
* PostgreSQL: ``SELECT enum_range(NULL::role_enum)`` =
  ``{admin,developer,user,service_token}`` — fully lowercase.
* All three existing user rows (admin / user / service_token) carry
  lowercase values after ``ALTER TYPE RENAME VALUE`` (RENAME is in-place
  on PG ≥ 10, no data ``UPDATE`` needed).
* ``curl /api/v1/users/me`` with the CF Access service token returns 200
  (was 500); response includes ``"role": "service_token"``.
* ``frontend/tests/e2e/phase13a-verify.spec.ts`` — 6 active passes + 2
  intentional skips. The spec also picked up unrelated bugs while running:
  ``/logout/i`` regex didn't match the rendered ``"Log out"`` (with space);
  ``waitUntil: "domcontentloaded"`` hung on the deployed CF Access path —
  switched to ``"commit"``; ``page.screenshot`` (both ``fullPage`` settings)
  hung when an open dialog/sheet covered the page — diagnostic shots removed.

The original "Suggested PR scope" above is preserved as historical context.
The actual PR scope is documented in the migration's docstring and this
section.
