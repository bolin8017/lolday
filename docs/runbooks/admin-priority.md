# Admin priority bump runbook

> Operator-facing. Use when a queued job needs to move ahead of others. Covers criteria, both UI and curl paths, side effects, audit, and rollback.
>
> Related: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §6.4–6.5 · `docs/runbooks/deploy.md`

## 1. When to bump

Bump only when the operational need is clear. Typical cases:

- A GPU=2 job submitted by a researcher has been sitting in `queued_backend` for an extended period while GPU=1 jobs from other users keep leapfrogging it. (Root cause: HEAD-not-fit halts FIFO iteration; a smaller job behind it does fit and was submitted later — so nothing dispatches the big job unless GPU space frees up all at once or someone bumps.)
- An urgent debug or production-incident repro session needs cluster time immediately.
- A long-queued job belongs to a time-sensitive deadline (paper submission, demo).

Do **not** bump as a workaround for a misconfigured job (wrong GPU count, wrong dataset). Fix the config, cancel the job, resubmit.

## 2. How to bump — frontend (preferred)

1. Sign in as an admin account (Cloudflare Access SSO).
2. Navigate to **Jobs** and find the target job (status: `queued_backend`).
3. Open the job detail panel.
4. Locate the **Priority** field (visible only to admins). The current value is `0` for normal jobs.
5. Click the field or the edit icon, enter an integer greater than 0 (e.g. `1`). Higher numbers sort first.
6. A confirmation warning is shown: "Bumping priority moves this job ahead of all jobs with lower priority. Other users' queued jobs may wait longer." Confirm.
7. Save. The UI refreshes and shows the new priority.

The FIFO reconciler picks up the change on its next 30-second cycle. No manual restart needed.

## 3. How to bump — curl

Requires a Cloudflare Access JWT for an admin account. Obtain the `CF_Authorization` cookie value from your browser's DevTools → Application → Cookies while logged in.

```bash
# Replace <host>, <job-id>, and <jwt> with real values.
curl -X PATCH "https://<host>/jobs/<job-id>" \
  -H "Cookie: CF_Authorization=<jwt>" \
  -H "Content-Type: application/json" \
  -d '{"priority": 1}'
```

Expected response: `200 OK` with updated `JobRead` body, showing `"priority": 1`.

Error cases:

| HTTP | Meaning                                                                                      |
| ---- | -------------------------------------------------------------------------------------------- |
| 403  | Caller is not admin, or job belongs to another user (non-admin cannot patch).                |
| 404  | Job not found.                                                                               |
| 409  | Job is not in `queued_backend` status — cannot change priority of a running or finished job. |
| 422  | Request body invalid (e.g. `priority` is negative or wrong type).                            |

## 4. Side effects

- The FIFO scheduler sorts by `(priority DESC, created_at ASC)`. A bumped job will be the next dispatched when it reaches HEAD and `cluster.free_gpu >= job.gpu_count`.
- All lower-priority `queued_backend` jobs from **all users** wait until the bumped job dispatches (or the cluster gains more GPU headroom).
- Running jobs are **not affected** — the FIFO scheduler only controls dispatch, not preemption.
- Multiple bumps are allowed. If two jobs both have `priority=1`, they sort by `created_at ASC` (older first).

## 5. Audit / observability

Backend logs record the priority change with the admin user's ID:

```
INFO  routes.jobs  priority bump: job_id=<uuid> old=0 new=1 by=<admin-email>
```

Each FIFO reconciler cycle logs which job is at HEAD and whether it was dispatched:

```
INFO  reconciler.fifo_scheduler  tick: queued=3 head=<uuid> gpu_needed=2 free=2 → dispatch
INFO  reconciler.fifo_scheduler  tick: queued=3 head=<uuid> gpu_needed=2 free=1 → blocked (HEAD not fit)
```

To tail live:

```bash
kubectl logs -n lolday deploy/backend -f | grep 'fifo_scheduler\|priority bump'
```

## 6. Rollback

Set priority back to 0 to undo a bump:

```bash
curl -X PATCH "https://<host>/jobs/<job-id>" \
  -H "Cookie: CF_Authorization=<jwt>" \
  -H "Content-Type: application/json" \
  -d '{"priority": 0}'
```

**Limitation:** if the bump already caused the job to be dispatched (status changed from `queued_backend` to `vcjob_pending` or later), reverting the priority has no effect — the vcjob is already in Volcano. You cannot withdraw a vcjob that has already started scheduling.

To cancel a job that has already been dispatched, use the cancel flow in the UI or the `DELETE /jobs/{id}` endpoint.

## 7. Cross-links

- Spec (design rationale + FIFO algorithm): `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §5.4, §6.4
- Deploy runbook: `docs/runbooks/deploy.md`
- Troubleshooting: `docs/runbooks/troubleshooting.md`
- Architecture §10 gotcha #16: `docs/architecture.md`
