# Audit-log retention — implementation plan

Spec: `docs/superpowers/specs/2026-06-02-audit-log-retention-design.md`
(approach C — scheduled indexed retention DELETE via the existing reconciler).

Single backend PR. TDD: tests first, then implementation, then run fast +
heavy tiers.

## Tasks

1. **Test (reconciler)** — `backend/tests/integration/reconciler/test_reconciler_audit_retention.py`:
   - seed a `User` (actor FK) + `AuditLog` rows with explicit `ts`
     (`now - timedelta(days=N+1)` → deleted; `now - timedelta(days=N-1)` →
     kept); call `reconcile_audit_retention(db_session)`; assert old gone,
     fresh kept, return count == 1.
   - `AUDIT_LOG_RETENTION_DAYS <= 0` (monkeypatch) → no-op, returns 0, both
     rows survive.
   - empty table → returns 0.
2. **Test (migration)** — `backend/tests/integration/migrations/test_migrations_audit_log_ts_index.py`:
   upgrade head → assert `ix_audit_log_ts` in `inspect(engine).get_indexes('audit_log')`;
   downgrade -1 → assert absent; upgrade head again.
3. **Config** — `app/config.py`: `AUDIT_LOG_RETENTION_DAYS: int = 365`.
4. **Module** — `app/reconciler/audit_retention.py`:
   `reconcile_audit_retention(session) -> int`.
5. **Loop** — `app/reconciler/loop.py`: `AUDIT_RETENTION_EVERY_N_ITERATIONS = 8640`
   - import + gated try/except block with
     `BACKEND_ERRORS.labels(stage="reconcile_audit_retention")`.
6. **Migration** — `uv run alembic revision -m "add ix_audit_log_ts"`; edit to
   `op.create_index("ix_audit_log_ts", "audit_log", ["ts"])` /
   `op.drop_index(...)`; `down_revision = "3a4b5c6d7e8f"`.
7. **Model** — `app/models/audit.py`: add `Index("ix_audit_log_ts", "ts")` to
   `__table_args__`; amend the append-only docstring.
8. **Docs** — append a closing note to the security postmortem follow-up #2
   pointing at this spec.

## Verify

```bash
cd backend && uv run alembic upgrade head           # apply on a dev DB
cd backend && uv run pytest -q                       # fast tier (incl. new tests)
cd backend && uv run pytest -m heavy -q              # real-PG whole-chain migrate
pre-commit run --files <changed>                     # ruff/mypy/format
```

## Done when

- New tests pass; full fast + heavy tiers green; pre-commit clean; PR merged
  with the per-area reconciler + migration tests in the same PR.
