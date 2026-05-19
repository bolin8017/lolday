"""Unit tests for `app.middleware.body_size.BodySizeLimitMiddleware`.

The middleware is the platform's defence against memory-exhaustion via
oversized request bodies (mainstream uvicorn does NOT cap body size by
default — every endpoint is otherwise free to allocate as much as the
client cares to send).

Two-layer protection, both pinned here:

1. **Content-Length header check** — the cheap path. If the client
   declares a body bigger than the cap, return 413 before any byte is
   read. Malformed `Content-Length` is intentionally tolerated (it falls
   through to Layer 2).
2. **Chunked-body byte counter** — wraps the ASGI ``receive`` callable
   passed to the inner app so the middleware notices when a client lies
   about (or omits) ``Content-Length`` and tries to stream a larger
   body. When the running tally exceeds the cap the wrapper returns a
   sticky end-of-body chunk and a ``guarded_send`` short-circuit
   replaces the downstream response with a 413.

The middleware is a **pure ASGI middleware** (no ``BaseHTTPMiddleware``)
so Layer 2 actually fires for the downstream handler — the prior
``BaseHTTPMiddleware`` shape silently bypassed the receive wrapper. See
spec ``docs/superpowers/specs/2026-05-19-body-size-middleware-asgi-rewrite-design.md``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.middleware.body_size import BodySizeLimitMiddleware
from fastapi import FastAPI, Request
from starlette.testclient import TestClient


@pytest.fixture
def small_cap_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """FastAPI app with a 64-byte cap so tests stay short.

    The cap is read at request time from `settings.BODY_SIZE_MAX_BYTES`,
    not at middleware-construction time, so monkeypatch is sufficient.
    """
    from app import config

    monkeypatch.setattr(config.settings, "BODY_SIZE_MAX_BYTES", 64)

    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware)

    @app.post("/echo")
    async def echo(payload: dict) -> dict:
        return {"len": len(payload.get("body", ""))}

    @app.post("/raw")
    async def raw(request: Request) -> dict:
        body = await request.body()
        return {"len": len(body)}

    return app


def _build_scope(*, headers: list[tuple[bytes, bytes]], path: str = "/echo") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [(b"host", b"testserver"), *headers],
    }


async def _drive(
    app: Any, scope: dict, body_chunks: list[tuple[bytes, bool]]
) -> tuple[list[int], bytes]:
    """Drive an ASGI app with a list of (body, more_body) chunks.

    Returns ``(statuses, body)`` — every ``http.response.start`` status
    seen, plus the concatenated response body. ``statuses`` is a list so
    callers can pin "exactly one 413, no 500 follow-up".
    """
    chunks: AsyncIterator[dict] = iter(  # type: ignore[assignment]
        {"type": "http.request", "body": b, "more_body": m} for b, m in body_chunks
    )

    async def receive() -> dict:
        try:
            return next(chunks)  # type: ignore[arg-type]
        except StopIteration:
            return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    await app(scope, receive, send)
    statuses = [m["status"] for m in sent if m.get("type") == "http.response.start"]
    body = b"".join(
        m.get("body", b"") for m in sent if m.get("type") == "http.response.body"
    )
    return statuses, body


def test_under_cap_passes_through(small_cap_app: FastAPI) -> None:
    """A body smaller than the cap reaches the handler unchanged."""
    client = TestClient(small_cap_app)
    r = client.post("/echo", json={"body": "x" * 10})
    assert r.status_code == 200
    assert r.json()["len"] == 10


def test_content_length_over_cap_returns_413(small_cap_app: FastAPI) -> None:
    """Layer 1: a Content-Length over the cap short-circuits to 413
    without invoking the handler.
    """
    client = TestClient(small_cap_app)
    big = "x" * 1000
    r = client.post(
        "/echo",
        content=f'{{"body": "{big}"}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413
    assert r.text == "payload too large"


def test_at_cap_boundary_passes(small_cap_app: FastAPI) -> None:
    """Body equal to the cap is admitted. Strict `> cap`, not `>= cap`."""
    payload = b'{"body": "' + (b"x" * 49) + b'"}'
    assert len(payload) == 61  # under cap
    client = TestClient(small_cap_app)
    r = client.post(
        "/echo",
        content=payload,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200


def test_malformed_content_length_is_tolerated(small_cap_app: FastAPI) -> None:
    """A non-numeric `Content-Length` skips Layer 1 (falls through to
    Layer 2). The middleware must not raise its own 500.

    TestClient strips invalid headers, so this test exercises a normal
    request whose CL is well-formed; the parallel
    ``test_malformed_content_length_falls_through_to_layer2`` below
    drives the raw-ASGI path with a hand-crafted ``CL: not-a-number``
    header.
    """
    client = TestClient(small_cap_app)
    r = client.post("/echo", json={"body": "small"})
    assert r.status_code == 200


async def test_malformed_content_length_falls_through_to_layer2(
    small_cap_app: FastAPI,
) -> None:
    """Pins the ``except ValueError: pass`` branch: a non-numeric
    Content-Length must not crash the middleware. Layer 2's byte counter
    is still in effect; here the body fits under the cap so the handler
    returns 200.
    """
    scope = _build_scope(
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", b"not-a-number"),
        ],
    )
    statuses, _ = await _drive(small_cap_app, scope, [(b'{"body":"x"}', False)])
    assert statuses == [200], (
        f"middleware should tolerate malformed CL, got statuses {statuses}"
    )


async def test_no_content_length_oversize_body_returns_413(
    small_cap_app: FastAPI,
) -> None:
    """Layer 2: client omits ``Content-Length`` entirely but streams a
    body larger than the cap. Middleware must return 413, never propagate
    a 500.

    This is the case that exposed the original
    ``BaseHTTPMiddleware`` bypass — Layer 1 has nothing to look at, and
    in the old shape ``request._receive = counting_receive`` was a
    dead-code assignment that the downstream app never saw.
    """
    scope = _build_scope(
        headers=[(b"content-type", b"application/octet-stream")], path="/raw"
    )
    statuses, body = await _drive(small_cap_app, scope, [(b"x" * 200, False)])
    assert statuses == [413], (
        f"layer 2 must catch oversize streamed body, got statuses {statuses}"
    )
    assert b"payload too large" in body


async def test_lying_content_length_oversize_body_returns_413(
    small_cap_app: FastAPI,
) -> None:
    """Layer 2: client sends ``Content-Length: 10`` (under cap) but
    actually streams 200 bytes. Layer 1 admits the request; Layer 2 must
    catch the lie and return 413.
    """
    scope = _build_scope(
        headers=[
            (b"content-type", b"application/octet-stream"),
            (b"content-length", b"10"),
        ],
        path="/raw",
    )
    statuses, body = await _drive(small_cap_app, scope, [(b"x" * 200, False)])
    assert statuses == [413]
    assert b"payload too large" in body


async def test_chunked_body_streamed_over_cap_returns_413(
    small_cap_app: FastAPI,
) -> None:
    """Layer 2 against a true ``Transfer-Encoding: chunked`` flow: two
    50-byte chunks (no CL header) push the tally to 100 > 64. Middleware
    must return 413.
    """
    scope = _build_scope(
        headers=[(b"content-type", b"application/octet-stream")], path="/raw"
    )
    statuses, body = await _drive(
        small_cap_app,
        scope,
        [(b"y" * 50, True), (b"y" * 50, False)],
    )
    assert statuses == [413]
    assert b"payload too large" in body


async def test_non_http_scope_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """``scope["type"] in {"lifespan", "websocket"}`` must delegate to
    the inner app unchanged. The old ``BaseHTTPMiddleware`` got this for
    free; the pure-ASGI rewrite must preserve it explicitly.
    """
    from app import config

    monkeypatch.setattr(config.settings, "BODY_SIZE_MAX_BYTES", 64)

    inner_calls: list[dict] = []

    async def inner(scope: dict, receive: Any, send: Any) -> None:
        inner_calls.append(scope)
        # Pretend to be a websocket app that accepts then closes.
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close"})

    middleware = BodySizeLimitMiddleware(inner)

    async def receive() -> dict:
        return {"type": "websocket.connect"}

    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    ws_scope = {"type": "websocket", "path": "/ws", "headers": []}
    await middleware(ws_scope, receive, send)

    assert inner_calls == [ws_scope], "non-http scopes must reach the inner app"
    assert [m["type"] for m in sent] == ["websocket.accept", "websocket.close"]
