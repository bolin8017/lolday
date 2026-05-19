# Design: Rewrite `BodySizeLimitMiddleware` as pure ASGI middleware

Date: 2026-05-19
Owner: backend

## 1. Problem

`BodySizeLimitMiddleware` (`backend/app/middleware/body_size.py`) ships
two layers of protection against memory-exhaustion via oversized
request bodies:

1. **Layer 1** — short-circuit when `Content-Length` exceeds
   `settings.BODY_SIZE_MAX_BYTES` (default 12 MiB).
2. **Layer 2** — wrap `request.receive` with a byte-counter that raises
   `RuntimeError("body too large")` once the running tally crosses the
   cap, then translate that to a 413 inside `dispatch`'s `try/except`.

The middleware subclasses `starlette.middleware.base.BaseHTTPMiddleware`.
Layer 2 has been **dead in production since the file was created** (PR
2026-05-12, security-hardening P1). The unit-test file
(`backend/tests/unit/middleware/test_body_size.py:195-203`) already
documents the bug:

> Starlette's `BaseHTTPMiddleware.call_next` runs the inner app on its
> own wrapped receive, bypassing `request._receive` — so a true
> end-to-end Layer-2 test that fires the wrapper from inside the inner
> app is not reachable without re-architecting the middleware.

### Why Layer 2 misbehaves under `BaseHTTPMiddleware`

`BaseHTTPMiddleware.__call__` constructs a separate anyio task group
that drives the downstream ASGI app on a _new_ `receive` callable
(`receive_or_disconnect` in Starlette 0.41+). That wrapper does _not_
call `request._receive` — it calls the original ASGI `receive` directly
(via an internal `wrapped_receive` closure). So `counting_receive` is
attached to `request._receive` but the downstream handler reads bytes
via a different code path that bypasses it.

Two concrete failure modes were verified empirically against the
current `main` branch (`fix/body-size-middleware-asgi` worktree on
2026-05-19) with `settings.BODY_SIZE_MAX_BYTES=64` and a 200-byte body:

| Client behaviour                                         | Expected | Actual                                                              |
| -------------------------------------------------------- | -------- | ------------------------------------------------------------------- |
| Sends `Content-Length: 200`                              | 413      | 413 (Layer 1)                                                       |
| Omits `Content-Length` entirely                          | 413      | `RuntimeError` escapes middleware → 500 from outer error middleware |
| Sends a lying `Content-Length: 10` but streams 200 bytes | 413      | `RuntimeError` escapes middleware → 500 from outer error middleware |

Layer 1 alone is not enough: HTTP/1.1 clients may send
`Transfer-Encoding: chunked` with no `Content-Length`, and HTTP/2
elides the header entirely (per RFC 9113 §8.1.2.6).

## 2. Goal

Layer 2 enforces the body cap end-to-end, returning a clean **413** to
clients that omit or lie about `Content-Length`. No 500s on oversized
streams.

## 3. Non-goals

- Changing the cap value (`12 MiB`) or its config knob.
- Changing middleware ordering in `app/main.py`.
- Adding per-route caps. The single global cap is intentional — the
  CSV-upload endpoint sits well under 10 MiB and that is the only large
  payload on the platform.
- Adding metrics. `BACKEND_ERRORS{stage="body_size_413"}` is a
  reasonable future Counter but not needed to close the correctness bug
  and would expand surface area unnecessarily.

## 4. Decision

Reimplement `BodySizeLimitMiddleware` as a **pure ASGI middleware**.
This is the mainstream pattern for body-size limits in Starlette /
FastAPI projects — Starlette's own
[`asgi-correlation-id`](https://github.com/snok/asgi-correlation-id)
and dozens of community body-cap middlewares use this shape; the
[Starlette documentation](https://www.starlette.io/middleware/#writing-pure-asgi-middleware)
explicitly recommends pure ASGI when "the middleware needs to send or
receive ASGI messages directly", which is exactly Layer 2's contract.

Mainstream practice cross-check (per CLAUDE.md §Mainstream practices
first): I checked `context7` for Starlette middleware docs as well as
[encode/starlette#1715](https://github.com/encode/starlette/issues/1715)
("BaseHTTPMiddleware receive replacement doesn't propagate") — both
land on the same recommendation: drop `BaseHTTPMiddleware`, write the
ASGI three-arg callable yourself.

### Shape

```python
class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cap = settings.BODY_SIZE_MAX_BYTES

        # Layer 1: Content-Length header check (unchanged semantics).
        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    if int(value) > cap:
                        await _send_413(send)
                        # Drain the body so the client doesn't hit a
                        # write error on a half-read socket. ASGI
                        # servers (uvicorn/hypercorn) handle this for
                        # us if we just return — they close the
                        # connection.
                        return
                except ValueError:
                    pass  # malformed CL — fall through to Layer 2
                break

        # Layer 2: byte-counting receive wrapper.
        received = 0
        oversized = False

        async def counting_receive() -> Message:
            nonlocal received, oversized
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > cap:
                    oversized = True
                    # Mark this chunk as the last so the downstream
                    # app's body() / stream() returns cleanly.
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
            return message

        # We need to short-circuit BEFORE the downstream app sends its
        # response. Wrap send so that if `oversized` flips to True
        # mid-stream we replace the response with a 413.
        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if oversized and not response_started:
                # Downstream tried to start a response without seeing
                # the body cap. Replace with 413.
                response_started = True
                await _send_413_messages(send)
                return
            if message["type"] == "http.response.start":
                if oversized:
                    response_started = True
                    await _send_413_messages(send)
                    return
                response_started = True
            await send(message)

        await self.app(scope, counting_receive, guarded_send)
```

### Why the `guarded_send` pattern over "raise and catch"

`counting_receive` _could_ raise (as today), but propagating an
exception out of an ASGI `receive` callable is brittle:

- Starlette's exception-handling middleware sees the raise and
  returns its own 500 response. We would have to special-case the
  exception further up the stack.
- The downstream handler may have already partially streamed a
  response. We cannot meaningfully turn that into a 413.

The `guarded_send` short-circuit is what
[`asgi-tools/asgi-tools`](https://github.com/klen/asgi-tools/blob/main/asgi_tools/middleware.py)
and several other body-cap middlewares do — set a sticky flag, let the
downstream finish reading what little body remains (it sees the cap as
EOF), then either replace the downstream response with 413 or, if the
downstream already started writing, log a warning and continue.

### Sticky-EOF semantics

When the cap is crossed, `counting_receive` returns an empty
`http.request` chunk with `more_body=False`. The downstream handler
reads this as a clean end-of-body — it does not see the oversize. The
handler may then:

1. Process the (now-truncated) body and try to return 200. We catch
   this in `guarded_send` and replace with 413.
2. Reject the truncated body itself (e.g. invalid JSON → 400). We
   _still_ replace with 413 — the client should see "you sent too
   much", not "your truncated body was malformed".
3. Stall waiting for more body. Cannot happen — `more_body=False`
   tells the handler the body is complete.

This means the **413 is final**: whatever the downstream handler does,
the client always sees `413 Payload Too Large`. That matches RFC 9110
§15.5.14 (the response that signals body-size violation) and the
existing Layer 1 contract.

## 5. Tests

Drop the dead-code disclaimer in the existing test file. Replace with
end-to-end ASGI tests that drive the new middleware via raw ASGI scope:

- `test_under_cap_passes_through` — body ≤ cap, no Content-Length,
  handler returns 200.
- `test_content_length_over_cap_returns_413` — Layer 1.
- `test_at_cap_boundary_passes` — body exactly at cap.
- `test_malformed_content_length_falls_through_to_layer2` — `CL: not-a-number`
  with under-cap body → 200.
- `test_no_content_length_oversize_body_returns_413` — **new**, the
  case that exposed the bug. No `Content-Length`, 200-byte body
  against 64-byte cap → 413.
- `test_lying_content_length_oversize_body_returns_413` — **new**.
  `Content-Length: 10` but actual body 200 → 413.
- `test_chunked_body_streamed_over_cap_returns_413` — **new**. Two
  `http.request` messages (50 + 50 bytes) with no `CL`; 100 > 64 cap
  → 413.
- `test_non_http_scope_passes_through` — `scope["type"]="lifespan"` /
  `"websocket"` → middleware delegates to `self.app` unchanged. (The
  current `BaseHTTPMiddleware` does this for us; the rewrite must
  preserve it explicitly.)

The legacy unit tests `test_counting_receive_raises_when_streamed_body_exceeds_cap`
and `test_dispatch_re_raises_non_body_runtime_errors` are deleted
together with the `dispatch()` method they pin (the new shape has no
`dispatch()` and no RuntimeError raise).

Integration tier — `backend/tests/integration/routers/test_body_size_middleware.py`
already covers the in-app Content-Length path against a 1 KB cap on
`/api/v1/credentials`. Re-run it against the new middleware; no test
change should be required.

## 6. Rollout

Single PR. No migration, no chart change, no operator action. The
middleware ships in the next backend container image (CI builds GHCR
artefact on merge; operator runs `bash scripts/deploy.sh` on
server30 to pick it up).

## 7. Risks

- **`guarded_send` short-circuit corrupts a streamed response**. The
  current handlers do not stream — every endpoint returns a single
  `JSONResponse` or similar. So `http.response.start` arrives once,
  before any body chunk. If a future endpoint starts streaming
  (Server-Sent Events, WebSocket upgrades), `guarded_send` would emit
  a 413 _after_ the start frame, which is invalid. We mitigate by
  setting `response_started` on the _first_ `http.response.start`,
  and gating the 413 swap on `not response_started`. Streamed
  endpoints will see the 413 swap _replace_ the entire response if
  the cap is exceeded _before_ the first byte was sent; if the cap is
  exceeded _after_, the client gets a truncated stream + connection
  close (better than a 500). The two existing streamed surfaces
  (`/healthz` SSE, `/jobs/.../events` WebSocket) do not consume the
  request body so they are not affected.

- **Larger middleware surface area to maintain**. Mitigated by tests
  that exercise both layers via raw ASGI; the new shape is ~60 lines.

## 8. Tech debt

None added. This _removes_ an existing §10 candidate (the dead Layer
2 disclaimer in the test file).

## 9. References

- Starlette docs — middleware writing pure ASGI:
  https://www.starlette.io/middleware/#writing-pure-asgi-middleware
- encode/starlette#1715 — BaseHTTPMiddleware receive replacement
  doesn't propagate
- RFC 9110 §15.5.14 — 413 Content Too Large
- asgi-tools — reference implementation of the
  counting-receive + guarded-send pattern:
  https://github.com/klen/asgi-tools/blob/main/asgi_tools/middleware.py
- Existing dead-code disclaimer:
  `backend/tests/unit/middleware/test_body_size.py:195-203`
- Existing P1 spec where the original middleware landed:
  `docs/superpowers/plans/2026-05-12-security-hardening-p1-stop-bleed.md` §body-size
