"""Reject request bodies that exceed ``settings.BODY_SIZE_MAX_BYTES``
before any handler reads them.

Two-layer protection:
1. If ``Content-Length`` is present and over the cap, 413 immediately.
2. Wrap the ASGI ``receive`` callable passed to the downstream app so
   that bodies streamed without (or with a lying) ``Content-Length``
   are still capped — when the running tally crosses the cap the
   wrapper emits a sticky end-of-body chunk and a paired ``send``
   guard replaces the downstream response with a 413.

Implemented as a **pure ASGI middleware** (no ``BaseHTTPMiddleware``).
The earlier ``BaseHTTPMiddleware`` shape silently bypassed Layer 2
because ``call_next`` did not propagate ``request._receive`` to the
downstream app. See
``docs/superpowers/specs/2026-05-19-body-size-middleware-asgi-rewrite-design.md``.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


async def _send_413(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", b"17"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"payload too large"})


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cap = settings.BODY_SIZE_MAX_BYTES

        for name, value in scope.get("headers", ()):
            if name == b"content-length":
                try:
                    if int(value) > cap:
                        await _send_413(send)
                        return
                except ValueError:
                    pass
                break

        received_bytes = 0
        oversized = False
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received_bytes, oversized
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                received_bytes += len(body)
                if received_bytes > cap:
                    oversized = True
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                if oversized and not response_started:
                    response_started = True
                    await _send_413(send)
                    return
                response_started = True
                await send(message)
                return
            if message["type"] == "http.response.body":
                if oversized:
                    return
                await send(message)
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)
