---
paths:
  - "backend/migrations/**"
---

# Alembic migration rules

## Filename convention (current)

Use alembic's auto-generated filename: `<rev>_<short_desc>.py`. Do not rename the prefix.

```bash
cd backend
uv run alembic revision --autogenerate -m "<short_desc>"
# alembic produces e.g. 7c19f8a2b441_add_job_priority_column.py
```

`<short_desc>` is snake_case, present-tense imperative ("add ...", "drop ...", "rename ...", "backfill ...").

> 2026-04-29 update: the previous `<rev>_phase<N>_<desc>.py` rename rule is **retired**. New revisions stay as alembic produces them. See `docs/conventions.md` §4 §6 for why.

## Historical phase-named migrations (do not rename)

These migrations were created under the previous `phaseN_X_` rename rule. They stay as-is — historical names are part of the audit trail.

| Filename | Phase (legacy) | What it does |
|----------|----------------|--------------|
| `d3f179666394_phase7_5_baseline.py` | 7.5 | First proper baseline; replaces `Base.metadata.create_all`. |
| `8a1c2d4e5f60_phase8_gpu2_profile.py` | 8 | GPU profile additions. |
| `b2e7c8a1f330_phase10_sso_admin_email.py` | 10 | Cloudflare SSO admin email. |
| `74c95d81f74e_phase11b_events_manifest.py` | 11b | Events / manifest schema. |
| `12f13a2e3d68_phase11c_drop_v0_schema_columns.py` | 11c | Drop v0 columns after retirement. |
| `c7e3a9b1d042_phase12_1_service_token_friendly_name.py` | 12.1 | Service-token friendly name + role enum patch 1. |
| `f9a2c4e8b01a_phase12_2_role_service_token.py` | 12.2 | Role enum patch 2. |
| `a4b8e7c91d52_phase12_3_role_enum_lowercase.py` | 12.3 | Role enum patch 3 (lowercase + `values_callable`). |
| `f91615e44fad_phase13a_detector_version_deleted_enum.py` | 13a | Detector version `deleted` enum. |
| `f37230063a20_phase13b_job_user_params_column.py` | 13b | Job `user_params` column. |

## Workflow

```bash
cd backend
uv run alembic revision --autogenerate -m "<short_desc>"
# manually review the generated migration — autogenerate is unreliable
uv run alembic upgrade head     # against a dev DB first
```

After upgrade, run `cd backend && uv run pytest backend/tests/test_migrations_*.py` if you wrote a migration test alongside.

## Never run `alembic downgrade` in production

Always roll forward with a new reverse migration. Downgrades are for local recovery, not deploy rollback.

## Enum gotchas (real history)

Phase 12.1 / 12.2 / 12.3 are three sequential patches against a single role_enum. The cause was SQLAlchemy `Enum` + Postgres `ENUM` type + lowercase value mismatch + missing `values_callable=lambda obj: [e.value for e in obj]`.

Read `docs/phase-history/phase12.1-role-enum-bug.md` before touching enums. Specifically:

- Use `values_callable` so SQLAlchemy emits the lowercase enum values that match the Postgres ENUM type.
- Do not assume autogenerate produces correct enum migrations — it does not.
- Do not change enum values in place; add new values via `ALTER TYPE`, deprecate old ones in a later migration.

## NOT NULL columns

Must ship with `server_default`, OR be split into a 2-step migration:

1. Add the column nullable.
2. Backfill existing rows.
3. (separate migration) `ALTER COLUMN ... SET NOT NULL`.

Single-step `ALTER TABLE ... ADD COLUMN x NOT NULL` without `server_default` will fail on existing rows.

## Schema head check is enforced at backend boot

`backend/app/main.py:_assert_schema_at_head()` raises `RuntimeError` if `alembic_version != head`. Forgetting `alembic upgrade head` is a CrashLoopBackOff, not a silent 500 at request time. The Helm `templates/alembic-upgrade-hook.yaml` Job is what runs it during `helm upgrade`.
