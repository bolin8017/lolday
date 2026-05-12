# mlflow_client AsyncClient per-call leak fix — Design Specification

> **Created 2026-05-12** (later same day as
> `2026-05-12-backend-httpx-client-leak-fix-design.md`). Promotes the
> deferred §6.1 follow-up to an active fix after post-deploy observation
> showed v0.21.1 still bleeds at ~0.9 MiB/min — projected OOM in ~5 h.

> **This spec answers**: which remaining call-site still creates `httpx`
> clients per request inside a tight loop, why the v0.21.1 partial fix is
> not enough, and what the same root-cause pattern looks like applied to
> the async variant.

## 1. Overview

After v0.21.1 (the gpu_signal Client-reuse fix) deployed at 14:21:52,
production memory dropped from the pre-fix 5 MiB/min linear growth to a
**residual 0.92 MiB/min** measured over a 90-minute window — meaningful
80% reduction, but extrapolation hits the 512 MiB chart limit in ~5 hours.
With zero non-terminal jobs and zero in-flight builds during the window,
this is a clean baseline leak; the only periodic activity hitting the
network is `reconciler_loop`'s `sync_model_versions` every ~60 s.

`backend/app/reconciler/model_sync.py` constructs a fresh
`MlflowClient(...)` on every tick; `MlflowClient._request` in turn opens
a fresh `httpx.AsyncClient` inside `async with` for every HTTP call. The
arena-fragmentation pattern is identical to the gpu_signal one already
fixed — only the async variant of `httpx.Client` is involved.

The math matches: 5 ticks per 5-minute window × ~0.8 MiB / AsyncClient
construction (the cost measured in §2.2 of the prior spec) = **~4 MiB / 5
min** ≈ the regular +4.3 MiB/5min cadence observed in production from
14:50 onward (table in §2.1 below).

## 2. Root cause analysis

### 2.1 Empirical evidence (post-v0.21.1 production curve)

| Time  | Memory    | Δ                  |
| ----- | --------- | ------------------ |
| 14:25 | 197.2 MiB | (baseline)         |
| 14:50 | 224.2 MiB | +27 MiB / 25 min   |
| 15:00 | 232.8 MiB | +4.3 MiB / 5 min   |
| 15:10 | 241.4 MiB | +4.3               |
| 15:20 | 250.0 MiB | +4.3               |
| 15:30 | 258.6 MiB | +4.3               |
| 15:40 | 266.4 MiB | +3.5               |
| 15:55 | 280.1 MiB | (+4.4 since 15:40) |

90-min slope: **0.92 MiB/min sustained linear**, no plateau. Pre-fix was
5.0 MiB/min — the 80% reduction confirms the gpu_signal fix landed, but
a secondary source remains.

### 2.2 Confounders ruled out

| Hypothesis                                          | Why ruled out                                                                              |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Job-driven mlflow finalize (`_finalize_mlflow_run`) | 0 jobs finished in the 30-min window per `psql` query                                      |
| Build reconciler                                    | 0 in-flight builds                                                                         |
| FIFO scheduler                                      | uses the v0.21.1 module-level `_HTTP_CLIENT` — verified by in-pod probe (`+0.03 MiB/iter`) |
| Reconciler iteration error                          | `lolday_backend_errors_total` rate = 0 over the window                                     |

### 2.3 Identified leaker

`backend/app/services/mlflow_client.py:43`:

```python
async with httpx.AsyncClient(timeout=self._timeout) as client:
    resp = await client.request(method, url, json=json, params=params)
```

Called by `sync_model_versions` (every 60 s) via
`client.search_model_versions()` → `_request("GET", "/model-versions/search", ...)`.

Identical anti-pattern to the gpu_signal one; same glibc arena
fragmentation mechanism. The fix is structurally identical, mapped to
`httpx.AsyncClient`'s async lifecycle.

## 3. Authorization

User authorised on 2026-05-12 (mid-observation):

- **Promote §6.1 follow-up to active fix** — production trajectory hits
  OOM in ~5 h at current rate
- **Same fix pattern** — module-level shared `AsyncClient`, lazy init
  under an `asyncio.Lock`, `close_http_client()` for lifespan teardown

## 4. Scope

### 4.1 In scope

1. `backend/app/services/mlflow_client.py` — module-level lazy
   `_HTTP_CLIENT: httpx.AsyncClient | None`, async
   `close_http_client()`, `_request` uses the shared Client.
2. `backend/app/main.py` lifespan teardown — `await
mlflow_client.close_http_client()` inside the existing `try/finally`.
3. `backend/tests/test_services_mlflow_client.py` — regression test
   asserting construction count == 1 across many `_request` calls;
   plus tests for close idempotency and post-close behaviour.
4. Cut release v0.21.2 (release-cut PR identical to v0.21.1's pattern).

### 4.2 Deferred — other AsyncClient per-call call sites

Grep across `backend/app/` found the same pattern in:

- `services/notify.py:38` — per-Discord-notification (rare; user/event-driven)
- `services/git.py:67,88` — per-build (rare)
- `services/harbor.py:38` — `_client()` factory method; needs read to
  confirm whether it returns a fresh instance per call
- `reconciler/projections.py:101,114` — per-job-completion (rare;
  triggered only when a job finalizes)

None of these run on a baseline-periodic cadence with zero traffic, so
none contribute to the observed leak today. They will start contributing
when traffic resumes. Tracked filename for follow-up:
`docs/superpowers/specs/2026-05-1X-async-client-singleton-rollout-design.md`.

## 5. Detailed design

### 5.1 Module-level lazy AsyncClient

```python
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_CLIENT_LOCK = asyncio.Lock()


async def _get_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it lazily under a lock.

    First caller's ``timeout`` wins (all real callers pass the same
    default 10 s); to override timeout per-request, use
    ``client.request(..., timeout=...)`` at the call site.
    """
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        async with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None:
                _HTTP_CLIENT = httpx.AsyncClient(timeout=timeout)
    return _HTTP_CLIENT


async def close_http_client() -> None:
    """Close the shared mlflow_client AsyncClient. Idempotent."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    client = _HTTP_CLIENT
    _HTTP_CLIENT = None
    try:
        await client.aclose()
    except Exception:
        BACKEND_ERRORS.labels(stage="mlflow_client_close").inc()
        logger.exception(
            "mlflow_client: AsyncClient.aclose() raised during shutdown"
        )
```

Why lazy (vs. eager like gpu_signal):

- `httpx.AsyncClient.__init__` binds internal anyio task-group machinery
  to the current event loop. Module import happens before the FastAPI
  event loop starts; eager construction at import time would bind to
  the wrong loop (or fail).
- The lock prevents two concurrent first-time callers from creating two
  separate Clients (race window between the unguarded `is None` check
  and the assignment).

### 5.2 `_request` rewrite

```python
async def _request(
    self, method, path, *, json=None, params=None,
) -> dict[str, Any]:
    url = f"{self._base}/api/2.0/mlflow{path}"
    client = await _get_http_client(self._timeout)
    last_exc: Exception | None = None
    for attempt in range(self._retries):
        try:
            resp = await client.request(method, url, json=json, params=params)
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = {"error_code": "UNKNOWN", "message": resp.text}
                return self._handle_error(resp.status_code, body)
            return resp.json() if resp.content else {}
        except httpx.HTTPError as e:
            last_exc = e
            await asyncio.sleep(0.2 * (attempt + 1))
    raise MlflowError(
        f"network error after {self._retries} retries: {last_exc!r}"
    )
```

The `MlflowClient.__init__` is unchanged — the class is still a thin
facade over a shared transport. Instances remain cheap (just hold the
base URL + retry count).

### 5.3 Lifespan teardown

In `backend/app/main.py`, inside the existing `try/finally` block
added by v0.21.1:

```python
finally:
    from app.services import gpu_signal, mlflow_client
    gpu_signal.close_http_client()
    await mlflow_client.close_http_client()  # NEW
```

`gpu_signal.close_http_client` is sync; `mlflow_client.close_http_client`
is async (uses `await client.aclose()`).

### 5.4 Tests

- `test_request_reuses_async_client` — counts
  `httpx.AsyncClient.__init__` invocations across 10 `_request` calls;
  asserts ≤ 1 (the lazy-init).
- `test_close_http_client_is_idempotent` — double call doesn't raise.
- `test_close_http_client_swallows_aclose_exceptions` — `aclose()` may
  raise during transport teardown; close must not propagate, and the
  post-close invariant (`_HTTP_CLIENT is None`) still holds.
- `test_request_after_close_creates_new_client` — calling `_request`
  after close lazy-initialises a fresh Client (idempotency of close
  does not prevent recovery). This differs from gpu_signal where the
  pattern raises `PrometheusUnavailable` — here the natural lazy-init
  shape means a post-close call simply re-creates the Client. Document
  the choice in the test docstring.

Existing `respx`-mocked tests should still pass — `respx` intercepts at
the httpx transport level, independent of whether the Client is fresh
or reused.

## 6. Verification plan

### 6.1 Unit tests

- `cd backend && uv run pytest tests/test_services_mlflow_client.py
tests/test_services_mlflow_client_v2.py`
- Full `uv run pytest`

### 6.2 Live in-pod probe

Reuse the `/tmp/inspect_pod.py` (or a variant) inside the v0.21.2 pod:
trigger `MlflowClient._request` 60 times and confirm RSS growth
< 5 MiB.

### 6.3 Production memory observation

After deploy, expect:

- 60-min sample: < 220 MiB (was 226 at this point in v0.21.1)
- 6-h sample: < 240 MiB (was projected at 196 + 360 × 0.92 = 527 MiB
  pre-fix, would have OOMed)
- 24-h sample: < 250 MiB

If the 60-min sample exceeds 240 MiB, a third leaker remains and we
investigate the §4.2 deferred list.

## 7. Risks

| Risk                                                                                 | Likelihood                                                                 | Mitigation                                                                         |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `_HTTP_CLIENT` reconstructed by every first-call after close in long-running tests   | Low — `close` is only called in lifespan teardown and explicit test resets | Idempotent close; lazy re-init by design                                           |
| `asyncio.Lock` at module load fails when imported without an event loop              | Low — `asyncio.Lock()` is event-loop-agnostic in Python 3.10+              | Documented in CPython release notes                                                |
| Different timeouts passed by different `MlflowClient` instances are silently ignored | Low — all real callers pass the same default 10 s                          | Document in `_get_http_client` docstring; per-request override is the escape hatch |
| `respx` test doubles don't apply to a long-lived Client                              | Low — `respx` patches at transport level, not Client-instance level        | Validated by existing tests passing without change in dev iteration                |

## 8. References

- Companion spec (gpu_signal fix that this depends on):
  `docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md`
- v0.21.1 release commit: `d010161`
- v0.21.1 fix commit: `a93a359`
- Live curve evidence: this spec §2.1
