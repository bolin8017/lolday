# Phase 6 Follow-ups

> **Purpose:** Carry-over list of items deferred or surfaced during Phase 6 (GPU FIFO + anti-starvation, PR #94). For next-session pickup.
>
> **Origin:** spec `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §3.2 + §10, plus the final-review report on the Phase 6 branch.

Items are grouped by category and tagged with **priority** (P0–P3) so next session can pick by impact. Each item has: source (spec section / review finding), goal, scope estimate, trigger to act, and suggested first move.

> **Status (2026-05-05, branch `phase6-followups`):**
>
> - **Resolved:** B1 (sync K8s wrapped via `asyncio.to_thread`), B2 (helper unification), B3 (pod-phase explicit form), C1 (architecture.md "thread"→"asyncio task" wording + `submitted_at` ordering), C2 + D1 (architecture.md §9 entries 16 + 17), A2 first move (`lolday_priority_bump_total` Counter wired into PATCH /jobs/{id}).
> - **Deferred — trigger not met:** A1 (backfill — needs production "HEAD waits, GPU idle" signal), A3 (depends on A2 data), E1 (smoke brittleness — current code works; trigger is "smoke breaks").

---

## Group A — Deferred features (need spec-first)

### A1. Slurm-style conservative backfill (P1) — DEFERRED

- **Source:** spec §3.2, §5.5.
- **Goal:** When HEAD job can't fit, allow younger jobs to run **only if** they finish before HEAD's predicted start. Prevents GPU idle while keeping strict FIFO for HEAD.
- **Substrate already in place:** Phase 5's `active_deadline_seconds` is an upper bound on runtime; sufficient for conservative backfill.
- **Scope:** medium-large. Backend reconciler logic change; new test scenarios. ~2–3 days incl. design + impl + smoke.
- **Trigger to act:** when production observation shows `lolday_jobs_pending_seconds` p99 dominated by "HEAD waits, GPU idle" pattern (i.e. cluster has free GPUs but reconciler refuses to schedule because HEAD can't fit). Without that signal, the marginal value is low — strict FIFO is currently working.
- **Suggested first move:** brainstorm a Phase 7 spec. Compare three flavours: EASY-BACKFILL (any younger job that fits), CONSERVATIVE (only if completes before HEAD's reservation), AGGRESSIVE (with explicit reservations). Slurm uses CONSERVATIVE — likely our pick. Reference Slurm's `--enable-backfill` semantics.
- **Deferral note (2026-05-05, `phase6-followups`):** Implementing speculatively without the production signal would be premature optimization (the doc's own trigger criterion). Mainstream engineering practice is "measure first, then build". `lolday_jobs_pending_seconds` is not yet emitted — adding that gauge would be the proper precursor; tracked as a separate observability item on the next phase touching `services/cluster_status.py`.

### A2. Aging — auto priority promotion for long-pending jobs (P2)

- **Source:** spec §3.2, §5.6.
- **Goal:** Automatically promote `priority` for any job whose `submitted_at` exceeds a threshold (e.g. 30 min). Reduces operator's manual bump burden.
- **Scope:** small-medium. Either inside `fifo_scheduler` (recompute effective priority each cycle as `priority + age_factor`) or via a background DDL task (UPDATE on schedule).
- **Trigger to act:** when admin bumps happen frequently enough to be a UX problem. Currently zero data — ISLab uses ~10 users.
- **Suggested first move:** measure first. Add a Prometheus counter for `priority_bump_total` increments in `PATCH /jobs/{id}` so we have signal. Check after 4 weeks. If > 1 per day, draft a small Phase 7 spec; if rare, defer indefinitely.
- **Status (2026-05-05):** First move done — `lolday_priority_bump_total` Counter wired into `PATCH /jobs/{id}` (counts only true priority changes, not no-op patches). Next checkpoint: 2026-06-02 (~4 weeks). If `rate(lolday_priority_bump_total[1d]) > 1` for 4 weeks, draft Phase 7 spec; else hold.

### A3. Per-user delegated priority permission / quota (P2) — DEFERRED

- **Source:** spec §3.2, §5.4. Plan §F.5 mentions "Phase 7+ if needed".
- **Goal:** Allow specific non-admin users to bump their own jobs' priority within a quota (e.g. 5 bumps/month). Or grant `priority:write` to a delegated user role.
- **Scope:** medium. New permission model in backend; UI changes; audit log.
- **Trigger to act:** when admin becomes a bottleneck. Same measurement source as A2.
- **Suggested first move:** hold pending A2's signal. If A2 happens, A3 might follow naturally.
- **Deferral note (2026-05-05, `phase6-followups`):** Strictly downstream of A2. The data needed to choose between A2 (auto-aging) and A3 (delegated permission) is the same `lolday_priority_bump_total` rate now being collected. Re-evaluate together with A2 at the 2026-06-02 checkpoint.

---

## Group B — Tech debt (code-only, no spec needed)

### B1. Sync K8s calls inside async functions (P1) — RESOLVED

- **Source:** Final-review code-quality finding (Important).
- **Resolution (2026-05-05, `phase6-followups`):** Every callsite of the sync `kubernetes.client` API inside an async function is now wrapped via `await asyncio.to_thread(...)`. `services/k8s.ensure_user_queue` was promoted to `async`. Mainstream Python pattern (Python docs explicitly recommend `asyncio.to_thread` for blocking I/O) was preferred over migrating to `kubernetes_asyncio` because: (a) keeps the official `kubernetes` library as single source of truth, (b) leaves the test stub layer unchanged (sync stubs run cleanly in the thread pool), (c) genuinely fixes the event-loop-blocking root cause without adding a third-party dependency. Affected modules: `services/{k8s,jobs_dispatch,harbor_init}.py`, `services/cluster_status.py` (wrapped at the router boundary because of the `@cached(TTLCache)` decorator), `reconciler/{fifo_scheduler,builds,log_capture,orphans,jobs}.py`, `routers/{cluster,detectors,jobs}.py` (also normalised the older `loop.run_in_executor` pattern in `detectors.py` to `asyncio.to_thread` for consistency). Tracked in `docs/architecture.md` §9 entry 16.

### B2. `_strategy_from_manifest` logic duplication (P3) — RESOLVED

- **Source:** Final-review code-quality finding (Minor).
- **Resolution (2026-05-05, `phase6-followups`):** Unified on the dict-input helper in `app/services/jobs_dispatch.py` (renamed from `_strategy_from_manifest_dict` → `_strategy_from_manifest`). Deleted the duplicated Pydantic-input helper from `app/routers/jobs.py` (it was dead production code — only the test file imported it). `test_routers_jobs.py` now calls the canonical helper with `manifest.model_dump()` at the boundary. New `None`-input regression test added.

### B3. `fifo_scheduler.py:74` pod-phase filter implicit None (P3) — RESOLVED

- **Source:** Final-review code-quality finding (Minor).
- **Resolution (2026-05-05, `phase6-followups`):** Replaced with explicit form: `if not pod.status or pod.status.phase not in ("Running", "Pending"): continue`. Bundled with B1 (same file).

---

## Group C — Documentation

### C1. `docs/architecture.md` "thread" wording for fifo_scheduler (P3) — RESOLVED

- **Source:** Final-review minor finding.
- **Resolution (2026-05-05, `phase6-followups`):** Updated `docs/architecture.md:343` (§10 item 16) and `.claude/rules/backend.md` (Phase 6 entry) to say "asyncio task" instead of "background thread". Also fixed `created_at` → `submitted_at` to match the actual ORDER BY in `fifo_scheduler.py`, and corrected the post-dispatch state to `JobStatus.PREPARING` (the enum value is `preparing`, not the doc's prior `vcjob_pending` literal).

### C2. Add B1 (sync K8s calls) to `docs/architecture.md` §9 tech debt list (P2) — RESOLVED

- **Source:** Architecture review during Phase 6.
- **Resolution (2026-05-05, `phase6-followups`):** Added §9 entry 16 to `docs/architecture.md`. Recorded as `~~resolved~~` (strikethrough convention used by the rest of §9) since B1 itself was fixed in the same branch.

---

## Group D — Upstream tracking (passive)

### D1. Volcano upstream issue #5044 (and related #4690, #3095) (P1 monitor / P3 act) — TRACKED

- **Source:** spec §4.5, §10.
- **What:** the bug that forced our application-layer FIFO pivot. `JobPipelinedFn` does not actually reserve idle resources for overdue PodGroups whose tasks can't fit. If upstream merges a fix, we may be able to simplify the backend FIFO scheduler — possibly reducing it to "submit immediately, let Volcano handle ordering" for trivial cases.
- **Cadence:** check the issue page once every 6–8 weeks. Set a Phase 7+ review reminder.
- **Trigger to act (rewrite):** when #5044 is closed AND the fix is in a Volcano release we can upgrade to. Don't act on a draft fix.
- **First move done (2026-05-05, `phase6-followups`):** Recorded as `docs/architecture.md` §9 entry 17 (open tracking item, not strikethrough) so the watch is visible alongside the rest of the tech-debt list.

---

## Group E — Test infrastructure

### E1. Smoke `SET session_replication_role = replica` brittleness (P2) — DEFERRED

- **Source:** Phase 6 Task H reviewer's "Important" caveat; validated working in Task J.
- **What:** `tests/2026-05-05-phase6-fifo-smoke.sh` bypasses the FK constraint on synthetic test rows by setting `session_replication_role = replica`. This requires the `lolday` Postgres user to have `REPLICATION` privilege. Currently works on server30, but a Postgres reinstall / Bitnami chart upgrade might revoke it.
- **Goal:** smoke that doesn't depend on this privilege.
- **Options:**
  - (a) Real detector_version: smoke first reads an existing detector_version_id from DB and uses it. Cleanup deletes test jobs (FK-safe). Drawback: cluster needs a detector to be seeded.
  - (b) Mock dispatch in test mode: backend grows a `FIFO_RECONCILER_MOCK_DISPATCH=true` env that makes `dispatch_job_to_volcano` no-op. Smoke flips it on, runs scenarios against real DB, flips off. Drawback: production-like code path with a test-mode flag is mildly distasteful but mainstream.
  - (c) Service-account admin token: investigate whether lolday can grow a test-only admin account that smoke uses for legitimate POST/PATCH calls. Per Phase 12.1 architecture, `Role.SERVICE_TOKEN: -1` blocks this — would require a new role.
- **Trigger to act:** if smoke breaks after a Postgres / chart change.
- **Suggested first move:** prefer option (a). Less code, no production-mode flags.
- **Deferral note (2026-05-05, `phase6-followups`):** Held off in this sweep because option (a) as written changes the test's _semantics_ — today the smoke deliberately uses a fake FK so dispatch fails fast (the failure log is what proves "HEAD was selected"). With a real detector_version the dispatch would succeed and create real vcjobs (or fail with ImagePullBackOff against a fake image). A clean redesign needs either (i) a new pre-dispatch observability log line in `fifo_scheduler` that names the HEAD without acting on it, or (ii) creating + cleaning up real Detector / DetectorVersion fixtures inside the smoke. Both are larger than a 1–2 hour sweep. Trigger ("smoke breaks after Postgres change") has not fired; defer until it does, at which point pick option (a-revised) with the new log line.

---

## Quick-action checklist for next session

> **Status (2026-05-05, branch `phase6-followups`):** the "clean up the trivial things" sweep is complete and the A2 "measure first" instrumentation is live.

If the goal is just **"clean up the trivial things from Phase 6"** without spec work:

- [x] Add B1 (sync K8s calls) entry to `docs/architecture.md` §9 (covers C2) — done; B1 itself also resolved.
- [x] B2 (`_strategy_from_manifest` unification)
- [x] B3 (pod-phase filter explicit form)
- [x] C1 (architecture.md "thread" wording)
- [x] D1 entry in §9 ("Tracking Volcano #5044 …")

If the goal is **"address an actual user-felt problem"**:

- A1 (backfill) is the highest-impact deferred feature. Brainstorm + spec it, then plan + implement. ~2–3 days. **Trigger:** production observation of "HEAD waits, GPU idle" pattern (`rate(lolday_jobs_pending_seconds[15m])` p99 dominated by the head-stall mode). Not yet observed.

If the goal is **"reduce admin bump burden"**:

- Measure first — `lolday_priority_bump_total` Counter is now wired (A2 first move done 2026-05-05). Re-evaluate after 4 weeks (~2026-06-02). If `rate(lolday_priority_bump_total[1d]) > 1`, draft an A2 (auto-aging) spec; A3 (per-user delegated priority) follows naturally once A2 ships.

---

## Source pointers

- Phase 6 spec: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`
- Phase 6 plan: `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase6-fifo-anti-starvation.md`
- Phase 6 PR: #94 on `bolin8017/lolday`
- Architecture tech debt list: `docs/architecture.md` §9
- Architecture gotchas: `docs/architecture.md` §10 items 15 + 16
