# Plan: Rewrite `BodySizeLimitMiddleware` as pure ASGI

Spec: `docs/superpowers/specs/2026-05-19-body-size-middleware-asgi-rewrite-design.md`
Branch: `fix/body-size-middleware-asgi`

## Tasks

1. **TDD — pin the bug first.** Rewrite
   `backend/tests/unit/middleware/test_body_size.py` to drive the
   middleware via raw ASGI scopes:
   - keep the four existing happy-path tests (Layer 1 over-cap, at-cap
     boundary, under cap, malformed CL tolerated)
   - add three new tests that fail against the current
     `BaseHTTPMiddleware` shape:
     - `test_no_content_length_oversize_body_returns_413`
     - `test_lying_content_length_oversize_body_returns_413`
     - `test_chunked_body_streamed_over_cap_returns_413`
   - add `test_non_http_scope_passes_through` (lifespan/websocket
     delegation invariant)
   - drop the two old dead-code disclaimer tests
     (`test_counting_receive_raises_when_streamed_body_exceeds_cap`,
     `test_dispatch_re_raises_non_body_runtime_errors`) — they pin
     the bug shape, not the fix.

2. **Verify RED.** Run
   `uv run pytest tests/unit/middleware/test_body_size.py -v` — expect
   three new tests to fail with `RuntimeError("body too large")`
   escaping the middleware.

3. **Implement the fix.** Replace `backend/app/middleware/body_size.py`
   with the pure ASGI shape from the spec §4:
   - module-level `_send_413(send)` helper
   - `class BodySizeLimitMiddleware: __init__(app); __call__(scope, receive, send)`
   - early-return for `scope["type"] != "http"`
   - Layer 1: iterate `scope["headers"]` for `content-length`
   - Layer 2: `counting_receive` returns sticky end-of-body when cap
     crossed; `guarded_send` swaps `http.response.start` → 413 if
     `oversized` flag is set.

4. **Verify GREEN.**
   - `uv run pytest tests/unit/middleware/test_body_size.py -v` — 9
     passes
   - `uv run pytest tests/integration/routers/test_body_size_middleware.py -v`
     — 2 passes (pinned the end-to-end Layer 1 contract; unchanged)

5. **Full suite + lint.**
   - `cd backend && uv run pytest -q` — confirm no regression on the
     1000+ unit/integration/contract/heavy tests
   - `pre-commit run --all-files` — ruff/mypy/yamllint clean

6. **Documentation.**
   - Spec already written (this PR's reference).
   - No `docs/architecture.md` §10 entry needed — the bug was never
     filed there; the dead Layer 2 was implicit in the test-file
     disclaimer. Closing comment in the PR description points at the
     fix file paths + spec.
   - No runbook / operations.md update needed (no operator action).

7. **Open PR.** Conventional commit + body referencing the spec.
   Wait for required checks (lint, backend-fast, backend-slow,
   frontend, frontend-slow, helm, images, helpers, dispatch).
   Squash-merge.

## Out of scope

- Other middlewares that subclass `BaseHTTPMiddleware`
  (`CSRFOriginMiddleware`, `RequestIDMiddleware`, `AuditLogMiddleware`,
  etc.). They do NOT manipulate the receive callable, so the
  `BaseHTTPMiddleware` bypass does not affect them. Audit deferred to
  a follow-up only if a separate bug surfaces.
- Adding a per-route or per-method cap.
- Adding a Prometheus counter for 413 hits — would be a useful
  observability follow-up but expanding scope here is unnecessary
  for the correctness fix.
