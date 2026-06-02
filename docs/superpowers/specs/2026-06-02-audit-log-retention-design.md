# Audit-log retention — design

- **Date**: 2026-06-02
- **Status**: accepted
- **Closes**: security-program follow-up #2 (`docs/postmortems/2026-05-12-security-audit-program.md` §342) — "Audit-log retention policy. Deferred from P5."
- **Supersedes** the original suggestion in `docs/superpowers/specs/2026-05-12-security-hardening-design.md` §6.5 / risk register (line 465): _"Add pg_partman monthly partitioning + 365-day retention"_. See §4.

## 1. Problem

The `audit_log` table (`backend/app/models/audit.py`) is **append-only**: the
sole writer `services/audit.py::write_audit_log` only ever `session.add()`s a
row, and the codebase has no UPDATE or DELETE path. P5 (security-hardening)
shipped the table + write path with acceptance "row exists"; a retention /
pruning policy was explicitly deferred. With no bound, the table grows
monotonically forever.

## 2. Live evidence (2026-06-02, server30)

Measured read-only against the production Postgres (pod `postgresql-0`, DB
`lolday`, alembic head `3a4b5c6d7e8f`):

- `audit_log`: **0 rows**, 64 kB total relation size, `MIN/MAX(ts)` both NULL.
- The DB is genuinely live (job_events=1620, job=16, user=3, detector=2).
- The table is empty because (a) the 3 users were created 2026-04-21,
  predating the audit_log feature (~2026-05-14), and (b) writes fire only at
  6 deliberately low-frequency security call sites (credential CRUD, dataset
  visibility/delete, detector register/delete, admin role-change, admin
  job-cancel, cross-user MLflow read, first-time user creation) — never
  per-request, never per-job-tick.

**Urgency is LOW / latent.** Even a busy multi-tenant deployment would write
on the order of tens–hundreds of audit rows/day. This is preventive hygiene
to close the append-only-with-no-bound gap, not a production fire.

## 3. Approaches considered

| #   | Approach                                                                  | Autonomously shippable?                                                                                                                                                                                                                                                                                                  | Verdict                                                                      |
| --- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| A   | `pg_partman` monthly partitioning + retention (the original spec text)    | **No** — the deployed image is the official `postgres:16-alpine` (raw StatefulSet, `values.yaml:129`), which does **not** bundle `pg_partman`. Needs a custom PG image + `shared_preload_libraries` while preserving the hardened securityContext (readOnlyRootFilesystem, drop ALL caps, uid 70) → operator dependency. | Rejected — over-engineered for the scale; operator-blocked.                  |
| B   | Native declarative range partitioning + a maintenance job                 | In principle (PG 16 core, no extension), but converting the **existing** populated table forces a PK rewrite (PK is uuid `id`; partitioning by `ts` requires composite `(id, ts)`) + table rebuild + per-partition index/FK recreation, plus a non-trivial down-migration.                                               | Rejected — high blast radius, unjustified below the partitioning break-even. |
| C   | **Scheduled, indexed retention DELETE driven by the existing reconciler** | **Yes** — no DB image change, no extension, no PK change. Reuses the reconciler cadence machinery that already runs `reconcile_orphan_token_secrets` (an age-based TTL reaper keyed off a Settings value).                                                                                                               | **Accepted.**                                                                |

Mainstream guidance (Crunchy Data, Data Egret, AWS RDS docs): partition-DROP
beats bulk-DELETE only past the bloat/VACUUM break-even — commonly cited as
roughly 10M rows / tens of GB. `audit_log` is orders of magnitude below that;
an indexed retention DELETE is the documented recommended path at this scale,
and autovacuum reclaims the freed space. This satisfies CLAUDE.md
"prefer open-source over custom code" (reuse the reconciler, add no infra),
"mainstream practices first", "deploy platform not dev platform", and
"don't over-engineer".

## 4. Why this supersedes the pg_partman suggestion

The P5 risk-register line proposed `pg_partman` before the official-image
extension gap was noticed (the post-program review even re-asked "still the
plan?"). The choice was a default-mainstream stub, never a hard requirement
(rated severity **L**). Given the empty/slow-growing table and the
extension's operator cost, approach C is the right-sized fix. If audit
volume ever crosses the partitioning break-even (e.g. a future change adds
per-request logging), revisit approach A under a dedicated spec.

## 5. Design

1. **Config** — add `AUDIT_LOG_RETENTION_DAYS: int = 365` to
   `app/config.py` (matches the spec's original "365-day retention"). A value
   `<= 0` disables the sweep (escape hatch).
2. **New module** — `app/reconciler/audit_retention.py` exporting
   `reconcile_audit_retention(session) -> int`:
   `DELETE FROM audit_log WHERE ts < now() - INTERVAL '<N> days'`, commit,
   return the deleted row count, log when non-zero. Mirrors the
   `reconcile_orphan_token_secrets` age-reaper shape.
3. **Loop wiring** — `app/reconciler/loop.py`: add
   `AUDIT_RETENTION_EVERY_N_ITERATIONS = 8640` (~24 h at the 10 s tick; the
   table grows slowly so daily is ample) and an
   `if iteration % AUDIT_RETENTION_EVERY_N_ITERATIONS == 0:` block calling it
   inside its own `try/except` →
   `BACKEND_ERRORS.labels(stage="reconcile_audit_retention").inc()` +
   `logger.exception(...)`. New `stage` is a sibling label on the existing
   Counter (bounded cardinality) per `.claude/rules/backend.md` §BACKEND_ERRORS;
   the pre-existing `LoldayBackendErrorRateElevated` alert covers it.
4. **Index migration** — one forward Alembic migration adds
   `ix_audit_log_ts` on `audit_log(ts)` so the range DELETE is index-driven
   rather than a seqscan as the table grows (the two existing composite
   indexes lead with non-`ts` columns, so neither serves a `ts`-only range
   predicate). Downgrade drops the index. The model `__table_args__` gains
   the matching `Index(...)` so model and schema stay in sync.
5. **Docstring** — `app/models/audit.py`: amend the "append-only: no UPDATE
   or DELETE path" statement to carve out this single sanctioned retention
   DELETE (there is still no UPDATE path).

No chart change, no extension, no new in-cluster pod.

## 6. Testing (per `.claude/rules/testing.md` per-area table)

- **Reconciler integration test** (`backend/app/reconciler/*.py` →
  reconciler integration test): seed `AuditLog` rows with explicit `ts`
  (one just outside the window → deleted, one just inside → kept); assert the
  boundary, the returned count, the disabled (`days <= 0`) no-op, and the
  empty-table no-op. `function`-scoped, no network, no `time.sleep`.
- **Migration up/down roundtrip** (`backend/migrations/*.py` → up/down
  roundtrip + real-PG heavy migrate): assert `ix_audit_log_ts` is present
  after `upgrade head` and absent after `downgrade -1` (aiosqlite). The new
  index migration is automatically exercised by the existing whole-chain
  heavy real-PG test (`tests/heavy/postgres/test_migrations_real_pg.py`).

## 7. Rollout

Ships in the regular backend image + `alembic upgrade head` (the pre-upgrade
hook Job applies the index migration). The sweep is a no-op until rows age
past the window, so there is no data-loss risk on first deploy. Operators can
tune or disable retention via `AUDIT_LOG_RETENTION_DAYS` without a code
change.

## 8. Out of scope

No audit-log reader UI, no `request_id` column, no `pg_partman` / `pg_cron`,
no retention for the model-registry audit tables (`model_*_log`) or
`job_events` — `job_events` (~324 rows/day during activity) is the more
pressing retention target and is tracked separately if it becomes one.
