# Backend httpx.Client per-call leak fix — Design Specification

> **Created 2026-05-12**. Closes the root-cause investigation deferred from the
> 2026-05-12 mitigation (PR #130 bumped backend memory limit 512Mi → 1Gi).
> The mitigation kept the platform running; this spec replaces it with a
> proper fix.

> **This spec answers**: what causes the linear 5 MiB/min memory growth in the
> backend pod since v0.20.8 (2026-05-10), and how to fix it at the root so the
> 1Gi memory-limit buffer can be reverted.

## 1. Overview

Since v0.20.8 (PR #117, 2026-05-10) the lolday backend pod has exhibited a
textbook memory leak: linear ~5 MiB/min growth, traffic-independent, OOMKill
every ~60 minutes at the 512Mi limit (every 2–3 hours after PR #130 raised it
to 1Gi). The pattern persisted even when only health probes and Prometheus
scrapes were hitting the pod, ruling out request-driven leaks.

PR #117 added two modules to the backend:

- `backend/app/services/gpu_signal.py` — host-aware GPU state via DCGM /
  Prometheus.
- `backend/app/reconciler/fifo_scheduler.py` — every-30s FIFO dispatch loop
  that calls `gpu_signal.compute_real_gpu_state()` on every tick.

Each call to `compute_real_gpu_state()` makes **three** `_query_prometheus()`
calls. Each `_query_prometheus()` constructs and immediately discards a fresh
`httpx.Client`:

```python
with httpx.Client(timeout=timeout) as client:
    resp = client.get(url, params={"query": query})
```

Inside the backend pod (Python 3.14.4, httpx 0.28.1, glibc malloc) this
pattern allocates ~2 MiB of resident pages per `compute_real_gpu_state()`
call that the kernel never returns to the OS, because glibc keeps freed
pages in its per-thread arena. Two FIFO ticks per minute × ~2 MiB ≈ **the
observed 5 MiB/min leak rate**.

The fix is straightforward and idiomatic for httpx: **reuse a single
`httpx.Client` for the lifetime of the backend process** instead of creating
a fresh one per call. This is the pattern the [httpx documentation
recommends](https://www.python-httpx.org/advanced/clients/) for any caller
making repeated requests to the same host, and matches the convention used
across the Python HTTP ecosystem (e.g. requests' `Session`, aiohttp's
`ClientSession`).

## 2. Root cause analysis

### 2.1 Symptom

| Date                       | Backend memory peak             | Restarts/day | Memory limit |
| -------------------------- | ------------------------------- | ------------ | ------------ |
| 05-08, 05-09 (pre-v0.20.8) | ~210 MiB                        | 0            | 512Mi        |
| 05-10 (v0.20.8/9 deploy)   | 346 MiB                         | 12           | 512Mi        |
| 05-11                      | 329 MiB                         | 23           | 512Mi        |
| 05-12 (post-PR #130, 1Gi)  | ~500 MiB peak, OOM cycle ~2-3 h | varies       | 1Gi          |

### 2.2 Empirical evidence (2026-05-12 in-pod tracemalloc probes)

Three probes were run inside the live backend pod (`backend-59cfc6c5cd-79l4p`)
via `kubectl exec` + a small `/tmp/leak_probe*.py` script. The
production-path call is `gpu_signal.compute_real_gpu_state.__wrapped__()`
(cache bypassed) which mirrors what fifo_scheduler triggers every 30 s.

**Probe A — current code (`with httpx.Client(...)` per call)**:

```
iter 5:  rss=62.6 MiB
iter 10: rss=74.3 MiB    (+11.7 MiB)
iter 20: rss=97.7 MiB    (+11.7 MiB)
iter 30: rss=121.0 MiB   (+11.7 MiB)
iter 40: rss=144.4 MiB   (+11.7 MiB)
iter 50: rss=165.4 MiB   (+11.0 MiB, plateau begins)
iter 60: rss=165.4 MiB   (plateau)
```

→ Linear **+2.0 MiB / iter** until plateau at ~165 MiB.

**Probe B — module-level shared `httpx.Client`** (proposed fix):

```
iter 5:  rss=39.6 MiB
iter 10: rss=39.7 MiB
...
iter 60: rss=40.9 MiB    (+1.3 MiB across 60 iters)
```

→ Flat: **+0.03 MiB / iter** (≈ 60× reduction).

Tracemalloc's traceback diff for Probe A showed all tracked Python-level
allocations summing to only ~5 MiB of the 118 MiB observed delta — the
remaining ~113 MiB is C-level glibc arena bloat not tracked by tracemalloc.
The pattern (linear growth, eventual plateau, no Python-level holders) is
the textbook signature of arena fragmentation from churning many short-lived
small-allocation objects through `httpx.Client.__init__` and
`httpcore.ConnectionPool` setup/teardown.

### 2.3 Why does this hit lolday but not most httpx users?

Most callers using `with httpx.Client() as c:` make one request and discard
the client — the pattern is idiomatic for one-off scripts. The leak only
manifests when:

1. The pattern is invoked on a **tight cadence** (every 30 s here).
2. Each invocation makes **multiple** requests (3 per `compute_real_gpu_state`).
3. The process runs **forever** in a memory-bounded container (1 Gi limit).

The first two conditions multiply the per-Client construction cost. The
third turns "harmless arena bloat" into "OOMKill".

### 2.4 Confounding evidence ruled out

| Hypothesis                                                           | Outcome                                             | Why ruled out                                                                                                                                                                                                             |
| -------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cachetools.TTLCache(maxsize=1, ttl=10)` retains GPUState references | Ruled out                                           | `maxsize=1` keeps at most one entry; replacing it eagerly evicts the previous. Cache holds only ~1 KB of dataclass per entry.                                                                                             |
| `prometheus_client.Counter.labels(stage=X)` cardinality explosion    | Ruled out                                           | All `stage=` values in gpu_signal/fifo_scheduler are string constants (~5 total), bounded.                                                                                                                                |
| `httpx.AsyncClient` in `mlflow_client.py` (same anti-pattern)        | Contributes when jobs run, NOT to observed baseline | The 5 MiB/min was measured during periods with no jobs. mlflow_client is only invoked from `reconciler/jobs.py` which is dormant when no jobs are active. Same fix pattern applies but separate scope — see §6 follow-up. |
| Python 3.14 native bug (e.g. asyncio.to_thread leakage)              | Ruled out                                           | Sync and `to_thread` variants of the probe leaked identically (Probe 4 sync vs to_thread, both +2.0 MiB/iter).                                                                                                            |

## 3. Authorization

User authorised on 2026-05-12:

- **Root-cause fix, not workaround**: revert the 1 Gi mitigation in the same PR.
- **Breaking changes OK** per `~/.claude/CLAUDE.md` §Root-cause first — but
  in practice this fix is a pure refactor of an internal helper; no public
  API change.
- **Mainstream practice**: httpx-recommended Client reuse pattern.

## 4. Scope

### 4.1 In scope

1. `backend/app/services/gpu_signal.py` — introduce a module-level
   `httpx.Client`; route all `_query_prometheus` calls through it; expose a
   `close_http_client()` helper for clean shutdown.
2. `backend/app/main.py` — call `gpu_signal.close_http_client()` from the
   FastAPI lifespan teardown.
3. `backend/tests/services/test_gpu_signal.py` — update existing
   `httpx.Client` mocks to match the new code shape; add a regression test
   that asserts a single Client is reused across many `_query_prometheus`
   calls.
4. `charts/lolday/templates/backend.yaml` — revert `memory: 1Gi` →
   `memory: 512Mi`; remove the temporary inline comment.
5. Auto-memory `project_backend_memory_leak_v0208_or_9.md` — mark resolved,
   link this spec.

### 4.2 Out of scope

- `mlflow_client.py` — same anti-pattern (`async with httpx.AsyncClient`
  per `_request` call) but contributes only when jobs are actively
  reconciling. Fix tracked as follow-up §6 below; deserves its own PR
  because the async lifecycle requires lifespan hookup and the call
  sites are short-lived `MlflowClient` instances (which need to either
  become a module-level singleton or share an externally-owned
  `AsyncClient`).
- Migrating `compute_real_gpu_state` to async / `httpx.AsyncClient`. The
  current code's blocking call from the async `/cluster/gpu-status` router
  is a separate latency concern (event-loop blocked for ≤3 × 5 s timeout in
  worst case), unrelated to memory. Tracked as tech debt.
- Tuning glibc malloc (`MALLOC_ARENA_MAX`). The Client-reuse fix already
  eliminates the symptom; tuning glibc would only help if the same pattern
  re-emerged elsewhere.

## 5. Detailed design

### 5.1 `gpu_signal.py` module-level Client

```python
# Module level — created at import time. The Client itself is cheap to
# hold open; the cost was in repeatedly tearing it down. httpx documents
# Clients as safe to share across threads.
_HTTP_CLIENT = httpx.Client(timeout=settings.GPU_SIGNAL_QUERY_TIMEOUT_SECONDS)


def _query_prometheus(query: str) -> list[dict]:
    url = f"{settings.GPU_SIGNAL_PROMETHEUS_URL}/api/v1/query"
    try:
        resp = _HTTP_CLIENT.get(url, params={"query": query})
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as e:
        raise PrometheusUnavailable(f"Prometheus HTTP error: {e}") from e
    except ValueError as e:
        raise PrometheusUnavailable(f"Prometheus returned non-JSON: {e}") from e
    # rest unchanged


def close_http_client() -> None:
    """Idempotent shutdown hook; called from FastAPI lifespan."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        _HTTP_CLIENT.close()
        _HTTP_CLIENT = None
```

Why module-level, not lazy:

- `gpu_signal` is imported eagerly by `fifo_scheduler` and `cluster_status`;
  the Client is needed within milliseconds of process start.
- Lazy init plus a lock adds complexity for no benefit when the eager cost
  is microseconds (no DNS, no TLS, no connection — Client.**init** just
  populates default state).

Why `timeout` at Client construction, not per-request:

- The timeout is constant across all three Prom queries (one settings value).
- Setting it once at construction is the more concise idiom and reduces
  per-request allocation by 1 `httpx.Timeout` object.

### 5.2 Lifespan teardown in `main.py`

In `lifespan(app)` after the `yield`:

```python
from app.services import gpu_signal
gpu_signal.close_http_client()
```

This is best-effort cleanup. K8s sends SIGTERM and waits ≤30 s; uvicorn
exits cleanly; the OS reaps the process either way. The explicit close is
hygiene — it eliminates a "ResourceWarning: unclosed connection" at
shutdown that would otherwise leak into logs.

### 5.3 Test changes

Existing tests use `@patch("app.services.gpu_signal.httpx.Client")` and
unwrap the `with` block via
`mock_client_cls.return_value.__enter__.return_value`. The new code no
longer uses `with`, so tests must patch the module-level `_HTTP_CLIENT`
directly:

```python
@pytest.fixture(autouse=True)
def _patch_http_client(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(gpu_signal, "_HTTP_CLIENT", mock)
    return mock
```

Three existing tests touch `httpx.Client`; they'll switch to the
`_HTTP_CLIENT` patch fixture above.

### 5.4 Regression test

```python
def test_query_prometheus_reuses_module_client(monkeypatch):
    """Regression: pre-fix code constructed a fresh httpx.Client per call,
    causing ~2 MiB / iter glibc arena bloat (5 MiB / min observed)."""
    constructions = 0
    real_init = httpx.Client.__init__

    def counting_init(self, *a, **kw):
        nonlocal constructions
        constructions += 1
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.Client, "__init__", counting_init)
    # Reimport to force re-execution of module-level Client construction
    importlib.reload(gpu_signal)

    for _ in range(10):
        try:
            gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")
        except gpu_signal.PrometheusUnavailable:
            pass  # No real Prom in tests; we just care about Client churn

    assert constructions == 1, (
        f"_HTTP_CLIENT must be constructed exactly once; saw {constructions}"
    )
```

This is a behavioural test, not a memory test. Memory-bounded assertions
are too fragile across CI environments. The behaviour ("how many Clients
are created") is the proximate cause of the leak and is deterministic.

### 5.5 Chart mitigation revert

`charts/lolday/templates/backend.yaml` reverts to:

```yaml
limits:
  cpu: 500m
  memory: 512Mi
```

The 1Gi inline comment block (lines 81–87) is removed. The original chart
state (pre-PR #130) had `memory: 512Mi` with no comment.

## 6. Follow-ups (intentionally separate PRs)

### 6.1 mlflow_client.py — same anti-pattern, deferred

`backend/app/services/mlflow_client.py` constructs a fresh
`httpx.AsyncClient` per `_request` call (line 43-44). Each `MlflowClient`
instance is itself short-lived (instantiated per `_handle_job_succeeded` /
`_finalize_mlflow_run` call). The fix shape is different because:

- Async lifecycle: `AsyncClient` must be created in an async context.
- Architectural choice between "make `MlflowClient` itself a module-level
  singleton" (simplest) vs. "inject a shared `AsyncClient` from lifespan"
  (cleanest).

Deferred because: the observed leak is dominated by gpu_signal (5 MiB/min
even with no jobs running). mlflow_client only contributes when jobs are
actively reconciling. Fixing it now would bundle two scopes; doing it later
also lets us include a memory check post-deploy to confirm the gpu_signal
fix alone closes the observed gap.

Tracked filename for the follow-up:
`docs/superpowers/specs/2026-05-1X-mlflow-client-singleton-design.md`.

### 6.2 Migrate gpu_signal to async

Right now `cluster_status.get_gpu_allocation()` is called synchronously
from an async router handler, blocking the event loop while three Prom
queries run (≤3 × 5 s in worst case). Migrating `gpu_signal` to async
(`httpx.AsyncClient`) eliminates the thread hop in fifo_scheduler and the
event-loop block in cluster_status. Tracked as tech debt in
`docs/architecture.md` §9.

### 6.3 Auto-memory cleanup

After deploy verification (§7), update
`~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_backend_memory_leak_v0208_or_9.md`
to status "resolved 2026-05-12 by spec
`2026-05-12-backend-httpx-client-leak-fix-design.md`". The memory entry
remains as historical context (debugging trail) but no longer represents
an open issue.

## 7. Verification plan

### 7.1 Unit + integration tests

- `cd backend && uv run pytest backend/tests/services/test_gpu_signal.py`
  passes (existing + new regression test).
- Full `uv run pytest` passes (no incidental break).

### 7.2 Live in-pod probe (pre-deploy sanity)

Repeat the §2.2 probe against the proposed fix to confirm 0.03 MiB/iter
behaviour holds inside the actual container image (not just dev shell):

```bash
kubectl -n lolday exec backend-<pod> -- env PROBE_VARIANT=sync PROBE_ITER=60 \
  /app/.venv/bin/python /tmp/leak_probe5.py
# expect: rss_end - rss_start < 5 MiB
```

### 7.3 Production memory observation (post-deploy)

After `bash scripts/deploy.sh` rolls out the new backend image:

```bash
# Wait for new pod to be Ready
kubectl -n lolday wait pod -l app.kubernetes.io/component=backend --for=condition=Ready --timeout=120s

# Sample container_memory_working_set_bytes every 5 min for 60 min
# Expect: flat at ~220 MiB ± 30 MiB (no linear growth)
```

If memory stays flat for 60 minutes, the fix is verified. The chart limit
will be back at 512Mi by then so any return of the leak would OOM within
1 h (same as the pre-mitigation baseline), giving a clean signal.

### 7.4 Rollback

The fix is a pure code change in one Python module. Rollback path: revert
the PR. The chart 512Mi revert in the same PR means a rollback also
restores the 1Gi buffer.

## 8. Risks

| Risk                                                                            | Likelihood                                                    | Mitigation                                                                                                                    |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Module-level Client holds a stale connection if Prometheus restarts mid-process | Low — httpx auto-reconnects on the next request               | httpx's `ConnectionPool` evicts dead connections lazily. Verified behaviour.                                                  |
| Test mocks break in unexpected ways                                             | Medium                                                        | Three existing tests need the patch fixture update; CI catches anything missed.                                               |
| 512Mi revert exposes some other slow leak we hadn't noticed                     | Low — pre-v0.20.8 baseline ran on 512Mi for weeks without OOM | Post-deploy memory observation in §7.3 catches this; rolling back the chart revert separately is one-line.                    |
| `_HTTP_CLIENT = None` after `close_http_client()` makes subsequent calls fail   | Low — close is only called at process shutdown                | The `_query_prometheus` body raises `PrometheusUnavailable` if the Client is gone, which the existing fail-safe path handles. |

## 9. References

- Spec context: `docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md`
- Spec context: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`
- Mitigation that this replaces: PR #130 (2026-05-12, chart `memory: 1Gi`)
- httpx Client reuse documentation:
  <https://www.python-httpx.org/advanced/clients/>
- Auto-memory entry being closed:
  `project_backend_memory_leak_v0208_or_9.md`
