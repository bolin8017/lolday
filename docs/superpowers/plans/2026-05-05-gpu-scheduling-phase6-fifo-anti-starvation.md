# Phase 6 v2 — GPU FIFO + Anti-Starvation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan revision:** v1 (chart-only sla plugin) was abandoned after live smoke proved Volcano 1.14 cannot do strict FIFO without preemption (Volcano upstream #5044). v2 (this plan) implements an application-layer FIFO scheduler in lolday backend.

**Goal:** Application-layer FIFO scheduler in lolday backend so multi-GPU jobs are not perpetually leapfrogged by smaller jobs, with admin-only priority bump for emergency reordering. Mainstream pattern (AWS Batch / Slurm without backfill).

**Architecture:** lolday backend attaches between user-submit and Volcano-submit. New job state `queued_backend`; new `priority` column; new reconciler thread `fifo_scheduler` runs every 30s, sorts by `(priority DESC, created_at ASC)`, submits HEAD to Volcano only when `cluster.free_gpu >= job.gpu_count`, else strict stop (no leapfrog). Admin can `PATCH /jobs/{id}` to bump priority. Volcano sub-chart returns to default scheduler config (sla plugin reverted; not load-bearing per §4.5/4.6 of spec).

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + asyncpg/aiosqlite + alembic, kubernetes Python client, React + TypeScript frontend, Helm.

**Spec:** [`docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`](../specs/2026-05-05-gpu-fifo-anti-starvation-design.md)

---

## Already done (in this branch, pre-pivot)

- Task 1 (`c204087`, `4b7c332`) — smoke test scaffold. Will be **rewritten** in Task H to test backend FIFO behavior instead of Volcano sla plugin.
- Task 2 (`4c5c729`) — 6a removed `requests.nvidia.com/gpu` from `lolday-jobs-quota`. **Keep.**
- Task 3 (`b836958`) — 6b added Volcano sla plugin to `scheduler_config_override`. **Will be reverted** in Task A.

## File Structure (changes in this plan)

**Modify:**

- `charts/lolday/values.yaml` — revert volcano.custom.scheduler_config_override (Task A)
- `backend/app/models/job.py` — add `priority` column, `JobStatus.queued_backend` (Task C)
- `backend/migrations/versions/<new>_phase6_priority_and_queued_backend.py` — new migration (Task B)
- `backend/app/routers/jobs.py` (or wherever POST /jobs lives) — change to write status=queued_backend, accept optional priority (Task E)
- `backend/app/routers/jobs.py` — add PATCH /jobs/{id} (Task F)
- `backend/app/main.py` (lifespan) — register fifo_scheduler reconciler thread (Task D)
- `frontend/src/...` — admin priority UI (Task G)
- `tests/2026-05-05-phase6-fifo-smoke.sh` — full rewrite (Task H)
- `docs/architecture.md`, `.claude/rules/backend.md`, `CLAUDE.md` (Task I)

**Create:**

- `backend/app/reconciler/fifo_scheduler.py` — new reconciler module (Task D)
- `docs/runbooks/admin-priority.md` — operator runbook (Task I)

---

## Task A: Revert sla plugin from chart (6b)

**Files:**

- Modify: `charts/lolday/values.yaml` — remove the `scheduler_config_override` block that was added in commit `b836958`. The `volcano.custom.metrics_enable: false` (Phase 9.5) stays.

- [ ] **Step A.1: Remove the scheduler_config_override block**

Find the block starting `# Phase 6 (2026-05-05): replace Volcano sub-chart default scheduler` and ending at the close of the `binpack` plugin (~22 lines). Delete the comment block and the `scheduler_config_override: |` literal-block scalar. Keep `metrics_enable: false` and its 8-line Phase 9.5 comment intact.

- [ ] **Step A.2: Verify with helm template**

```bash
helm template lolday charts/lolday \
  --set harbor.harborAdminPassword=x --set fernetKey=x \
  --set postgresql.password=x --set mlflow.dbPassword=x \
  --set monitoring.kps.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=x \
  --set monitoring.alertmanager.discord.criticalWebhookUrl=https://discord.com/api/webhooks/1/aA \
  --set monitoring.alertmanager.discord.warningWebhookUrl=https://discord.com/api/webhooks/1/aA \
  2>/dev/null \
  | awk '/scheduler.yaml$/,/^---$/' \
  | grep -c "name: sla"
```

Expected: `0` (sla plugin no longer in rendered ConfigMap).

- [ ] **Step A.3: pre-commit + commit**

```bash
pre-commit run --files charts/lolday/values.yaml
git add charts/lolday/values.yaml
git commit -m "$(cat <<'EOF'
revert(charts): phase 6b — drop Volcano sla plugin from scheduler config override

Live smoke test E proved sla plugin's JobPipelinedFn does not actually
reserve resources for overdue PodGroups (Volcano upstream issue #5044
OPEN since 2025). Keeping the plugin in scheduler_config_override is
dead code — it never triggers the behavior we expected. Revert to
Volcano sub-chart default scheduler config; FIFO + anti-starvation will
be implemented in lolday backend (see spec §6.3-§6.5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B: Alembic migration — `priority` column + `queued_backend` enum value (6c.3)

**Files:**

- Create: `backend/migrations/versions/<auto-generated>_phase6_priority_and_queued_backend.py`

- [ ] **Step B.1: Inspect current Job model + status enum**

```bash
cd backend
grep -n "class JobStatus\|priority\|class Job" app/models/job.py | head -20
ls migrations/versions/ | sort | tail -5
uv run alembic current
```

Note the current alembic head and the existing `JobStatus` enum values for use in migration.

- [ ] **Step B.2: Create migration**

```bash
cd backend
uv run alembic revision -m "phase6 priority and queued_backend status"
```

- [ ] **Step B.3: Edit the migration file**

Implement `upgrade()`:

- Add column: `op.add_column("jobs", sa.Column("priority", sa.Integer, nullable=False, server_default="0"))`
- Add index: `op.create_index("ix_jobs_priority", "jobs", ["priority"])`
- Add enum value: For Postgres, use `op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'queued_backend'")`. For aiosqlite (test) the enum is a string column so no DDL needed; check Lolday's existing migration patterns (see e.g. `backend/migrations/versions/` for previous enum extensions).

Implement `downgrade()`:

- `op.drop_index("ix_jobs_priority", table_name="jobs")`
- `op.drop_column("jobs", "priority")`
- Do NOT drop the enum value (Postgres native enum doesn't support that easily; left as accepted limitation)

- [ ] **Step B.4: Test migration up + down on aiosqlite**

```bash
cd backend
uv run pytest tests/test_alembic_smoke.py -v   # if such file exists; otherwise:
DATABASE_URL=sqlite+aiosqlite:///:memory: uv run alembic upgrade head
DATABASE_URL=sqlite+aiosqlite:///:memory: uv run alembic downgrade -1
DATABASE_URL=sqlite+aiosqlite:///:memory: uv run alembic upgrade head
```

Expected: migrations run cleanly without error. (Postgres-only DDL like ALTER TYPE may need branching in the migration; check existing pattern in lolday's migrations.)

- [ ] **Step B.5: Commit**

```bash
git add backend/migrations/versions/
git commit -m "$(cat <<'EOF'
feat(migrations): phase 6c — priority column and queued_backend status

Adds `priority INTEGER NOT NULL DEFAULT 0` column with index, and
extends JobStatus enum with `queued_backend` value. Used by the new
backend FIFO scheduler (spec §6.3, §6.4) to hold jobs before Volcano
submission and order them by (priority DESC, created_at ASC).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task C: Job ORM update (6c.1, 6c.2)

**Files:**

- Modify: `backend/app/models/job.py`
- Modify: `backend/app/schemas/job.py` (or wherever the Pydantic Job schemas live)

- [ ] **Step C.1: Add `priority` to ORM**

Edit `backend/app/models/job.py`:

```python
priority: Mapped[int] = mapped_column(
    sa.Integer, nullable=False, default=0, index=True,
    doc="FIFO priority. 0 = normal. Higher values move job ahead in backend FIFO. Admin-only mutation (Phase 6).",
)
```

- [ ] **Step C.2: Add `queued_backend` to JobStatus enum**

Edit the `JobStatus` enum in `backend/app/models/job.py`:

```python
class JobStatus(StrEnum):
    queued_backend = "queued_backend"   # NEW: phase 6 — backend FIFO holding state
    # ... existing values ...
```

Place `queued_backend` in the ordering that matches conceptual lifecycle (likely before any "submitted" / "running" state).

- [ ] **Step C.3: Update Pydantic schemas**

Find the Job response schema (e.g. `JobOut` or similar in `backend/app/schemas/`). Add `priority: int = 0` field. For request schema (`JobCreate` or similar), add optional `priority: int | None = None`.

- [ ] **Step C.4: Run mypy + ruff**

```bash
cd backend
uv run ruff check app/models/job.py app/schemas/job.py
uv run mypy app/models/job.py
```

Expected: pass.

- [ ] **Step C.5: Commit**

```bash
git add backend/app/models/job.py backend/app/schemas/
git commit -m "$(cat <<'EOF'
feat(backend): phase 6c — Job.priority field + queued_backend status

ORM additions matching the alembic migration. Pydantic schemas exposed
priority field for API responses; request schema accepts optional
priority (admin-only enforcement happens in router).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task D: Backend FIFO reconciler (6d)

**Files:**

- Create: `backend/app/reconciler/fifo_scheduler.py`
- Modify: `backend/app/main.py` — register the new reconciler thread in the FastAPI lifespan
- Create: `backend/tests/reconciler/test_fifo_scheduler.py`

- [ ] **Step D.1: Write the unit-test skeleton (TDD-first)**

In `backend/tests/reconciler/test_fifo_scheduler.py`, write tests that exercise:

1. Empty queue → no-op.
2. Single job, gpu_count=1, cluster.free_gpu=2 → submits.
3. Single job, gpu_count=2, cluster.free_gpu=1 → does NOT submit.
4. Two jobs at same priority — older `created_at` submits first.
5. Two jobs at different priorities — higher priority submits first regardless of `created_at`.
6. HEAD doesn't fit → no further jobs are tried (strict FIFO assertion).
7. submit_to_volcano raises → job stays at `queued_backend`, no rollback to wrong state.

Use SQLAlchemy aiosqlite + a mock K8s client. Refer to existing test patterns in `backend/tests/` for fixtures.

Run them, expect FAIL (function not implemented yet):

```bash
cd backend
uv run pytest tests/reconciler/test_fifo_scheduler.py -v
```

Expected: all 7 tests fail with `ModuleNotFoundError` or similar.

- [ ] **Step D.2: Implement `fifo_scheduler.py`**

`backend/app/reconciler/fifo_scheduler.py` exports an async function `reconcile_fifo_queue()`:

```python
async def reconcile_fifo_queue(session: AsyncSession, k8s_client: ...) -> None:
    """Submit queued_backend jobs to Volcano in strict FIFO order.

    Sorts by (priority DESC, created_at ASC). For each candidate, submits
    only if cluster.free_gpu >= job.gpu_count. HEAD that doesn't fit
    halts the loop (strict FIFO; no leapfrog).
    """
    free_gpu = await _compute_cluster_free_gpu(session, k8s_client)
    queued = await session.execute(
        select(Job)
        .where(Job.status == JobStatus.queued_backend)
        .order_by(Job.priority.desc(), Job.created_at.asc())
    )
    for job in queued.scalars():
        if free_gpu < job.gpu_count:
            break  # strict FIFO
        try:
            await _submit_to_volcano(job, k8s_client)
            job.status = JobStatus.submitted   # whatever the post-submit state is
            await session.commit()
            free_gpu -= job.gpu_count
        except Exception as e:
            await session.rollback()
            log.error("submit failed for job %s: %s", job.id, e)
            # job stays at queued_backend; next cycle retries
            continue
```

`_compute_cluster_free_gpu` reads K8s pods in `lolday-jobs` ns + DB jobs that are post-submit-but-not-yet-pod-running. Return `physical_gpu_count - allocated`. See spec §6.4.2 for exact calculation.

`_submit_to_volcano` extracts the existing vcjob-create logic that's currently inside `routers/jobs.py` (the POST /jobs handler). Refactor to be importable from both places.

- [ ] **Step D.3: Re-run tests**

```bash
cd backend
uv run pytest tests/reconciler/test_fifo_scheduler.py -v
```

Expected: all 7 PASS.

- [ ] **Step D.4: Register the reconciler in lifespan**

Edit `backend/app/main.py` lifespan to start a background asyncio task that calls `reconcile_fifo_queue` every `FIFO_RECONCILER_PERIOD_SECONDS` (env var, default 30) seconds. Cancel cleanly on shutdown.

Pattern: see existing reconciler/jobs.py registration if it has one; otherwise model after fastapi best practices for `asyncio.create_task` in lifespan.

- [ ] **Step D.5: Run full backend test suite**

```bash
cd backend
uv run pytest -v
```

Expected: all green. New reconciler tests pass; nothing existing breaks.

- [ ] **Step D.6: Commit**

```bash
git add backend/app/reconciler/fifo_scheduler.py backend/app/main.py backend/tests/reconciler/
git commit -m "$(cat <<'EOF'
feat(backend): phase 6d — application-layer FIFO scheduler

New reconciler module fifo_scheduler.py runs every 30s. Pulls jobs
with status=queued_backend from DB, sorts by (priority DESC,
created_at ASC), submits HEAD to Volcano only when cluster.free_gpu
>= job.gpu_count. HEAD-not-fit halts the loop (strict FIFO; no
leapfrog) — the design goal from spec §5.

Refactored vcjob submission logic out of routers/jobs.py so both the
POST /jobs path and the reconciler can call it. Reconciler thread is
registered in main.py lifespan.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task E: API — POST /jobs change (6e.1)

**Files:**

- Modify: `backend/app/routers/jobs.py` — POST handler

- [ ] **Step E.1: Update integration test**

Find the existing test for POST /jobs (likely `backend/tests/routers/test_jobs.py` or similar). Add assertions:

- New job's status is `queued_backend` (was previously `running` or `submitted` — adjust expected value)
- Default `priority` is 0
- A non-admin user posting `{"priority": 5}` gets 403 with message about admin-only
- An admin user posting `{"priority": 5}` gets 200 with the new job, priority=5

Run:

```bash
cd backend
uv run pytest tests/routers/test_jobs.py -v
```

Expected: at least the new assertions FAIL (status mismatch or 403 not raised).

- [ ] **Step E.2: Modify POST /jobs handler**

In the POST handler:

1. Replace `submit_to_volcano(job)` with: write Job row with `status=queued_backend`, return job. Do not call vcjob-create here.
2. Accept optional `priority: int | None` from request body (default `None` → `0`).
3. If `priority` is non-zero AND requester is not admin (`current_user.role != Role.ADMIN`), raise `HTTPException(403, detail="priority field is admin-only")`.
4. If admin, persist the supplied priority.

- [ ] **Step E.3: Run tests**

```bash
cd backend
uv run pytest tests/routers/test_jobs.py -v
```

Expected: all green, including the new assertions.

- [ ] **Step E.4: Commit**

```bash
git add backend/app/routers/jobs.py backend/tests/routers/
git commit -m "$(cat <<'EOF'
feat(backend): phase 6e — POST /jobs writes queued_backend status

POST /jobs no longer submits the vcjob synchronously. The job is
persisted to the DB with status=queued_backend; the new fifo_scheduler
reconciler (Phase 6d) picks it up. Optional priority field is accepted
only from admin users; non-admin priority != 0 is rejected with 403.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task F: API — PATCH /jobs/{id} for priority (6e.2)

**Files:**

- Modify: `backend/app/routers/jobs.py` — add PATCH handler
- Modify: `backend/app/schemas/job.py` — add JobPatch schema

- [ ] **Step F.1: Write integration tests**

New test file or add to `backend/tests/routers/test_jobs.py`:

- Admin PATCH /jobs/{id} body `{"priority": 5}` on a queued_backend job → 200, priority updated.
- Non-admin PATCH same → 403.
- Admin PATCH on a job in non-queued_backend state (e.g. running) → 422 with `priority cannot be changed after job has been submitted to Volcano`.
- PATCH non-existent job id → 404.

Run tests, expect FAIL (handler not implemented):

```bash
cd backend
uv run pytest tests/routers/test_jobs.py -k "patch" -v
```

- [ ] **Step F.2: Add JobPatch Pydantic schema**

In `backend/app/schemas/job.py`:

```python
class JobPatch(BaseModel):
    priority: int | None = None
```

- [ ] **Step F.3: Add PATCH handler**

In `backend/app/routers/jobs.py`:

```python
@router.patch("/{job_id}")
async def patch_job(
    job_id: UUID,
    body: JobPatch,
    user: User = Depends(require_role(Role.ADMIN)),
    db: AsyncSession = Depends(get_async_session),
) -> JobOut:
    # Fetch job
    # If status != queued_backend → raise 422
    # Apply body.priority if provided
    # Commit, return JobOut
```

- [ ] **Step F.4: Run tests**

```bash
cd backend
uv run pytest tests/routers/test_jobs.py -v
```

Expected: all green.

- [ ] **Step F.5: Commit**

```bash
git add backend/app/routers/jobs.py backend/app/schemas/ backend/tests/
git commit -m "$(cat <<'EOF'
feat(backend): phase 6e — PATCH /jobs/{id} for admin priority bump

Admin-only endpoint. Accepts {"priority": int} body; only valid for
jobs in status=queued_backend. Already-submitted jobs reject with 422
(priority cannot change after Volcano submission, by design — the
scheduler decision has already been made).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task G: Frontend — admin priority UI (6f)

**Files:**

- Modify: `frontend/src/pages/Jobs/JobList.tsx` (or equivalent)
- Modify: `frontend/src/pages/Jobs/JobDetail.tsx` (or equivalent)
- Modify: `frontend/src/pages/Jobs/JobSubmitForm.tsx` (or equivalent)
- Modify: `frontend/src/api/jobs.ts` — add patchJob

- [ ] **Step G.1: Add API client function**

In `frontend/src/api/jobs.ts`:

```typescript
export async function patchJob(jobId: string, body: { priority?: number }) {
  return apiClient.patch(`/jobs/${jobId}`, body).then((r) => r.data);
}
```

- [ ] **Step G.2: Add priority input to admin submit form**

In the job submit form, gate `priority` input on `useAuth().role === "admin"`. Default 0. Validate it's a non-negative integer.

- [ ] **Step G.3: Add priority column to admin job list**

In the job list page, conditionally render a `Priority` column when current user is admin. Inline-edit for queued_backend jobs (call `patchJob`). For other statuses, render read-only.

- [ ] **Step G.4: Add priority field + edit UI to admin job detail**

Same pattern. Show priority value; admin can edit if `status === "queued_backend"`.

- [ ] **Step G.5: UX warning before bumping**

Before submitting a non-zero priority, show a confirm dialog or inline warning text:

> Bumping priority pauses submission of new lower-priority jobs to Volcano until this job is dispatched. Running jobs are not affected.

- [ ] **Step G.6: Tests**

Add vitest/RTL test for admin-only visibility of priority controls. Add Playwright test for the edit flow if e2e tests cover this area.

```bash
cd frontend
pnpm test
pnpm playwright test --grep "priority"   # if applicable
```

- [ ] **Step G.7: Commit**

```bash
git add frontend/src/
git commit -m "$(cat <<'EOF'
feat(frontend): phase 6f — admin priority UI for jobs

Admin sees a Priority column / field in job list / detail / submit form
and can edit it inline (calls PATCH /jobs/{id}). Regular users do not
see priority controls. Inline warning before bumping explains the side
effect (pauses lower-priority submission until this job dispatches).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task H: Smoke test rewrite (6g)

**Files:**

- Modify: `tests/2026-05-05-phase6-fifo-smoke.sh`

- [ ] **Step H.1: Rewrite from scratch**

Replace the existing scheduler-config-patch smoke with API-driven smoke. Two scenarios:

**(a) Strict FIFO test:**

```
1. Cluster fully empty.
2. Admin POST /jobs gpu=2 (job-A) → status=queued_backend
3. Admin POST /jobs gpu=1 (job-B), within 5 seconds → status=queued_backend
4. Wait up to 90s for both to run.
5. Assert: job-A.startTime < job-B.startTime
```

**(b) Priority bump test:**

```
1. Cluster fully empty.
2. Admin POST /jobs gpu=1 (job-X) → priority=0, status=queued_backend
3. Admin POST /jobs gpu=2 (job-Y) → priority=0, status=queued_backend
4. Admin PATCH /jobs/{job-Y.id} body={"priority": 1}
5. Wait for next reconciler cycle (≤ 30s)
6. Assert: in (priority DESC, created_at ASC) order, job-Y is the head
7. Assert eventually: job-Y.startTime < job-X.startTime
```

Authentication: use a service token with admin role created at test setup. Pattern modeled after existing Phase smoke tests' auth approach (see `tests/2026-05-05-phase2-fair-share-smoke.sh` for service-token usage).

Cleanup: trap deletes test jobs at the end.

- [ ] **Step H.2: Run smoke against deployed cluster**

```bash
bash tests/2026-05-05-phase6-fifo-smoke.sh
```

Expected: PASS both scenarios.

- [ ] **Step H.3: Commit**

```bash
git add tests/2026-05-05-phase6-fifo-smoke.sh
git commit -m "$(cat <<'EOF'
test(phase6): rewrite smoke for backend-layer FIFO

Replaces the obsolete sla-plugin smoke (which was based on a wrong
assumption about Volcano JobPipelinedFn behavior — see spec §4.5)
with API-driven assertions: (a) strict FIFO across two admin-submitted
jobs, (b) priority bump moves a younger job ahead of an older one.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task I: Documentation (6h)

**Files:**

- Modify: `docs/architecture.md` — §10 entries
- Create: `docs/runbooks/admin-priority.md`
- Modify: `.claude/rules/backend.md` — note new reconciler thread
- Modify: `CLAUDE.md` — "How to navigate" entry for backend FIFO

- [ ] **Step I.1: Update architecture.md §10**

Find the §10 entries that mention Phase 1 quota and Phase 2 queue. Update:

- Phase 1 entry: note that GPU axis was removed in Phase 6a; cross-link to spec.
- Phase 2 entry: note that Phase 6 added backend-layer FIFO; sla plugin was attempted and reverted (cross-link to spec §4.5/4.6).
- Add new Phase 6 entry summarizing: backend FIFO reconciler, priority field, admin-only mutation, cross-link to spec + runbook.

- [ ] **Step I.2: Create runbook**

`docs/runbooks/admin-priority.md`. Sections:

- When to bump priority (criteria)
- How to bump (frontend + curl PATCH example)
- Side effects (UX warning text from G.5)
- Audit / observability (where to see the priority change)
- Rollback (if you bumped wrongly)

- [ ] **Step I.3: Update .claude/rules/backend.md**

Add a bullet near the existing reconciler.py mention noting the new `fifo_scheduler.py` thread, its 30s cadence, and that it reads/writes `Job.status` and `Job.priority`.

- [ ] **Step I.4: Update CLAUDE.md "How to navigate"**

Add an entry like:

```
- backend FIFO scheduler (Phase 6) → docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md, docs/runbooks/admin-priority.md
```

- [ ] **Step I.5: pre-commit + commit**

```bash
pre-commit run --files docs/architecture.md docs/runbooks/admin-priority.md .claude/rules/backend.md CLAUDE.md
git add docs/ .claude/ CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(phase6): record backend-layer FIFO + admin priority architecture

architecture.md §10: Phase 1 quota / Phase 2 queue entries updated to
reference Phase 6a removal and Phase 6 backend FIFO. New runbook for
admin priority bumps. .claude/rules/backend.md mentions new reconciler
thread. CLAUDE.md adds a navigation entry.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task J: Deploy + verify smoke passes

**Files:** none (deploy + verification only).

- [ ] **Step J.1: Build and push backend image**

The backend code changed; new image must be built and pushed to Harbor. Check operator runbook for the exact command (typically `bash scripts/build-helpers.sh` covers helpers, but backend image is a separate manual flow per `.claude/rules/charts-and-helm.md`). The flow:

```bash
docker buildx build --platform linux/amd64 -t harbor.lolday.svc:80/lolday/lolday-backend:v0.18.0-phase6 backend/
docker push harbor.lolday.svc:80/lolday/lolday-backend:v0.18.0-phase6
```

(Adjust tag scheme to match repo convention. Check the `charts/lolday/values.yaml:backend.image` value for current pattern.)

- [ ] **Step J.2: Update chart's backend.image tag**

Edit `charts/lolday/values.yaml:backend.image:` to point to the new tag.

- [ ] **Step J.3: Source secrets and deploy**

```bash
source .lolday-secrets.env
bash scripts/deploy.sh
```

Expected: deploy succeeds. Backend pod restarts with new image; alembic upgrade hook runs the new migration.

- [ ] **Step J.4: Verify cluster state**

```bash
# alembic head matches migrations directory
kubectl -n lolday exec deploy/backend -- uv run alembic current

# Job model includes priority
kubectl -n lolday exec deploy/postgresql -- psql -U postgres -d lolday -c "\d jobs" | grep priority

# JobStatus enum includes queued_backend
kubectl -n lolday exec deploy/postgresql -- psql -U postgres -d lolday -c "SELECT enum_range(NULL::jobstatus);"

# fifo_scheduler logs show it's running
kubectl -n lolday logs deploy/backend | grep fifo_scheduler | tail -5

# scheduler ConfigMap no longer has sla plugin (Task A reverted it)
kubectl -n lolday get cm lolday-scheduler-configmap -o jsonpath='{.data.volcano-scheduler\.conf}' | grep -c "name: sla"
# Expected: 0
```

- [ ] **Step J.5: Run smoke**

```bash
bash tests/2026-05-05-phase6-fifo-smoke.sh
```

Expected: PASS both scenarios from Task H.1.

- [ ] **Step J.6: No commit** (verification only).

---

## Task K: Final review + finishing branch

- [ ] **Step K.1: Dispatch final code reviewer subagent**

Range: from `a1ac644` (origin/main) to current HEAD on branch `feat/gpu-fifo-anti-starvation`. Review against spec.

- [ ] **Step K.2: Address any review findings**

Fix in new commits (no amending) — pre-commit hooks must pass.

- [ ] **Step K.3: Bump chart version (operator decision)**

Optionally bump `charts/lolday/Chart.yaml` to v0.18.0 if cutting a release for Phase 6 alone. Skip if bundling for a combined release later.

- [ ] **Step K.4: Invoke superpowers:finishing-a-development-branch**

Discuss with operator whether to merge directly, open PR, or keep branch open for further iteration.

---

## Summary

11 tasks total (A–K). Pre-existing tasks (Task 1–3 from v1 plan) — Task 1 (smoke scaffold) and Task 2 (quota change) are kept as foundation; Task 3 (sla plugin) is reverted by Task A.

Net new commits expected on `feat/gpu-fifo-anti-starvation`:

- A revert sla
- B alembic migration
- C ORM update
- D reconciler + tests
- E POST /jobs change
- F PATCH /jobs/{id}
- G frontend
- H smoke rewrite
- I docs
- (J no commit)
- K1–K4 (reviewer findings + version bump)

Total ~9–11 commits, all under conventional-commits format. Each task is self-contained enough for a fresh subagent dispatch.
