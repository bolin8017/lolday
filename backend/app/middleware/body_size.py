"""Reject request bodies that exceed ``settings.BODY_SIZE_MAX_BYTES``
before any handler reads them.

Two-layer protection:
1. If ``Content-Length`` is present and over the cap, 413 immediately.
2. Wrap ``request.receive`` so chunked bodies that exceed the cap mid-
   stream also error out, never reaching the handler.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cap = settings.BODY_SIZE_MAX_BYTES
        # Layer 1: Content-Length header check.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > cap:
                    return Response(
                        content="payload too large",
                        status_code=413,
                        media_type="text/plain",
                    )
            except ValueError:
                pass  # malformed CL — let Starlette deal with it later

        # Layer 2: wrap receive to count bytes as they arrive (defends
        # against missing / lying Content-Length).
        received_bytes = 0
        original_receive = request.receive

        async def counting_receive():
            nonlocal received_bytes
            message = await original_receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                received_bytes += len(body)
                if received_bytes > cap:
                    raise RuntimeError("body too large")
            return message

        request._receive = counting_receive
        try:
            return await call_next(request)
        except RuntimeError as e:
            if str(e) == "body too large":
                return Response(
                    content="payload too large",
                    status_code=413,
                    media_type="text/plain",
                )
            raise
