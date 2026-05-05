# Phase 6 Follow-ups

> **Purpose:** Carry-over list of items deferred or surfaced during Phase 6 (GPU FIFO + anti-starvation, PR #94). For next-session pickup.
>
> **Origin:** spec `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §3.2 + §10, plus the final-review report on the Phase 6 branch.

Items are grouped by category and tagged with **priority** (P0–P3) so next session can pick by impact. Each item has: source (spec section / review finding), goal, scope estimate, trigger to act, and suggested first move.

---

## Group A — Deferred features (need spec-first)

### A1. Slurm-style conservative backfill (P1)

- **Source:** spec §3.2, §5.5.
- **Goal:** When HEAD job can't fit, allow younger jobs to run **only if** they finish before HEAD's predicted start. Prevents GPU idle while keeping strict FIFO for HEAD.
- **Substrate already in place:** Phase 5's `active_deadline_seconds` is an upper bound on runtime; sufficient for conservative backfill.
- **Scope:** medium-large. Backend reconciler logic change; new test scenarios. ~2–3 days incl. design + impl + smoke.
- **Trigger to act:** when production observation shows `lolday_jobs_pending_seconds` p99 dominated by "HEAD waits, GPU idle" pattern (i.e. cluster has free GPUs but reconciler refuses to schedule because HEAD can't fit). Without that signal, the marginal value is low — strict FIFO is currently working.
- **Suggested first move:** brainstorm a Phase 7 spec. Compare three flavours: EASY-BACKFILL (any younger job that fits), CONSERVATIVE (only if completes before HEAD's reservation), AGGRESSIVE (with explicit reservations). Slurm uses CONSERVATIVE — likely our pick. Reference Slurm's `--enable-backfill` semantics.

### A2. Aging — auto priority promotion for long-pending jobs (P2)

- **Source:** spec §3.2, §5.6.
- **Goal:** Automatically promote `priority` for any job whose `submitted_at` exceeds a threshold (e.g. 30 min). Reduces operator's manual bump burden.
- **Scope:** small-medium. Either inside `fifo_scheduler` (recompute effective priority each cycle as `priority + age_factor`) or via a background DDL task (UPDATE on schedule).
- **Trigger to act:** when admin bumps happen frequently enough to be a UX problem. Currently zero data — ISLab uses ~10 users.
- **Suggested first move:** measure first. Add a Prometheus counter for `priority_bump_total` increments in `PATCH /jobs/{id}` so we have signal. Check after 4 weeks. If > 1 per day, draft a small Phase 7 spec; if rare, defer indefinitely.

### A3. Per-user delegated priority permission / quota (P2)

- **Source:** spec §3.2, §5.4. Plan §F.5 mentions "Phase 7+ if needed".
- **Goal:** Allow specific non-admin users to bump their own jobs' priority within a quota (e.g. 5 bumps/month). Or grant `priority:write` to a delegated user role.
- **Scope:** medium. New permission model in backend; UI changes; audit log.
- **Trigger to act:** when admin becomes a bottleneck. Same measurement source as A2.
- **Suggested first move:** hold pending A2's signal. If A2 happens, A3 might follow naturally.

---

## Group B — Tech debt (code-only, no spec needed)

### B1. Sync K8s calls inside async functions (P1)

- **Source:** Final-review code-quality finding (Important).
- **Where:** `backend/app/reconciler/fifo_scheduler.py:72` (`k8s.list_namespaced_pod`), `backend/app/services/jobs_dispatch.py:113, 137–143, 148–151`. **Pre-existing pattern** in lolday's reconciler — Phase 6 surfaced it but did not introduce it.
- **Goal:** Replace `kubernetes` (sync client) calls with `kubernetes_asyncio` (or wrap in `asyncio.to_thread`) so the asyncio event loop never blocks on K8s API. Latency-sensitive when the API server hiccups.
- **Scope:** medium. Affects more than just Phase 6 code (any reconciler module that calls K8s). Requires a sweep + careful test of all reconciler paths.
- **Trigger to act:** when a K8s API slowdown causes user-visible API latency / 504 responses on the backend. Today's load is low and the risk is theoretical.
- **Suggested first move:** add to `docs/architecture.md` §9 as a numbered tech-debt entry (not yet there). Then schedule as a refactor PR — no spec needed; it's mechanical replacement with tests.

### B2. `_strategy_from_manifest` logic duplication (P3)

- **Source:** Final-review code-quality finding (Minor).
- **Where:** `backend/app/routers/jobs.py:85` (Pydantic-input version) and `backend/app/services/jobs_dispatch.py:44` (dict-input version, named `_strategy_from_manifest_dict`). Phase 6 introduced `jobs_dispatch.py` and copied the helper instead of unifying.
- **Goal:** Single source of truth. Either accept both input shapes via overload, or normalise to dict at one call site.
- **Scope:** small. ~30 minutes including tests.
- **Trigger to act:** when a third caller appears, or when adding a new strategy (e.g. `"horovod"`) — the change-in-two-places friction is the moment to consolidate.
- **Suggested first move:** unify on the dict version (it's the more general one); update router to convert Pydantic → dict at the call boundary. Small PR, no design needed.

### B3. `fifo_scheduler.py:74` pod-phase filter implicit None (P3)

- **Source:** Final-review code-quality finding (Minor).
- **Where:** `backend/app/reconciler/fifo_scheduler.py:74` — `if (pod.status and pod.status.phase) not in ("Running", "Pending"): continue`. Functionally correct but relies on `(None and X)` short-circuit evaluating to `None`, which then falls into the "not in tuple" branch by coincidence.
- **Goal:** Replace with explicit form: `if not pod.status or pod.status.phase not in ("Running", "Pending"): continue`. Same behaviour, clearer intent.
- **Scope:** trivial.
- **Trigger to act:** next time the file is touched for any reason. Or bundle with B2.

---

## Group C — Documentation

### C1. `docs/architecture.md` "thread" wording for fifo_scheduler (P3)

- **Source:** Final-review minor finding.
- **Where:** `docs/architecture.md:343` says fifo_scheduler "runs a background thread every 30s". Actually it's an `asyncio.create_task` (asyncio task), not a `threading.Thread`.
- **Goal:** Replace "thread" with "asyncio task" for accuracy.
- **Scope:** trivial.
- **Trigger to act:** bundle with B2/B3 or any docs touch in §10.

### C2. Add B1 (sync K8s calls) to `docs/architecture.md` §9 tech debt list (P2)

- **Source:** Architecture review during Phase 6.
- **Where:** §9 currently goes up to item 15 (resolved). Add item 16 (or fold into existing structure) for "Sync K8s calls in async backend code". Cross-link to B1 above.
- **Trigger to act:** at the same time as B1 is scheduled, so the debt is visibly tracked.

---

## Group D — Upstream tracking (passive)

### D1. Volcano upstream issue #5044 (and related #4690, #3095) (P1 monitor / P3 act)

- **Source:** spec §4.5, §10.
- **What:** the bug that forced our application-layer FIFO pivot. `JobPipelinedFn` does not actually reserve idle resources for overdue PodGroups whose tasks can't fit. If upstream merges a fix, we may be able to simplify the backend FIFO scheduler — possibly reducing it to "submit immediately, let Volcano handle ordering" for trivial cases.
- **Cadence:** check the issue page once every 6–8 weeks. Set a Phase 7+ review reminder.
- **Trigger to act (rewrite):** when #5044 is closed AND the fix is in a Volcano release we can upgrade to. Don't act on a draft fix.
- **Suggested first move:** add a single line to `docs/architecture.md` §9 noting the watch ("Tracking Volcano #5044 — backend FIFO may simplify when upstream lands the fix").

---

## Group E — Test infrastructure

### E1. Smoke `SET session_replication_role = replica` brittleness (P2)

- **Source:** Phase 6 Task H reviewer's "Important" caveat; validated working in Task J.
- **What:** `tests/2026-05-05-phase6-fifo-smoke.sh` bypasses the FK constraint on synthetic test rows by setting `session_replication_role = replica`. This requires the `lolday` Postgres user to have `REPLICATION` privilege. Currently works on server30, but a Postgres reinstall / Bitnami chart upgrade might revoke it.
- **Goal:** smoke that doesn't depend on this privilege.
- **Options:**
  - (a) Real detector_version: smoke first reads an existing detector_version_id from DB and uses it. Cleanup deletes test jobs (FK-safe). Drawback: cluster needs a detector to be seeded.
  - (b) Mock dispatch in test mode: backend grows a `FIFO_RECONCILER_MOCK_DISPATCH=true` env that makes `dispatch_job_to_volcano` no-op. Smoke flips it on, runs scenarios against real DB, flips off. Drawback: production-like code path with a test-mode flag is mildly distasteful but mainstream.
  - (c) Service-account admin token: investigate whether lolday can grow a test-only admin account that smoke uses for legitimate POST/PATCH calls. Per Phase 12.1 architecture, `Role.SERVICE_TOKEN: -1` blocks this — would require a new role.
- **Trigger to act:** if smoke breaks after a Postgres / chart change.
- **Suggested first move:** prefer option (a). Less code, no production-mode flags.

---

## Quick-action checklist for next session

If the goal is just **"clean up the trivial things from Phase 6"** without spec work:

- [ ] Add B1 (sync K8s calls) entry to `docs/architecture.md` §9 (covers C2)
- [ ] B2 (`_strategy_from_manifest` unification)
- [ ] B3 (pod-phase filter explicit form)
- [ ] C1 (architecture.md "thread" wording)
- [ ] D1 entry in §9 ("Tracking Volcano #5044 …")

These five together = ~1–2 hour PR, no design needed, all minor.

If the goal is **"address an actual user-felt problem"**:

- A1 (backfill) is the highest-impact deferred feature. Brainstorm + spec it, then plan + implement. ~2–3 days.

If the goal is **"reduce admin bump burden"**:

- Measure first (instrument `PATCH /jobs/{id}` calls), wait 4 weeks, then decide on A2 / A3.

---

## Source pointers

- Phase 6 spec: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`
- Phase 6 plan: `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase6-fifo-anti-starvation.md`
- Phase 6 PR: #94 on `bolin8017/lolday`
- Architecture tech debt list: `docs/architecture.md` §9
- Architecture gotchas: `docs/architecture.md` §10 items 15 + 16
