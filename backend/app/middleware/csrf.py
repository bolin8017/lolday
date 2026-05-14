"""M-csrf (security-hardening P6) -- CSRF Origin / Sec-Fetch-Site middleware.

Rejects ``POST/PUT/PATCH/DELETE`` requests on ``/api/v1/*`` unless the
request signals a same-origin browser intent via either:
  1. ``Sec-Fetch-Site: same-origin`` or ``Sec-Fetch-Site: none``, or
  2. ``Origin`` whose scheme://host[:port] matches the ``Host`` header.

Fails open when both headers are absent -- that's structurally non-browser
traffic (CLI, CF Access service tokens, Python httpx without explicit
Origin). Browsers cannot suppress both on cross-site fetches, so a real
CSRF attempt always carries at least an ``Origin``. See plan section D1.

Excluded paths:
  - ``/api/v1/internal/*`` -- job-token-authed; isolated on :8001 per P2,
    and this prefix is exempt as defense-in-depth.
  - ``/api/v1/mlflow-authz`` -- Traefik ForwardAuth target, server-to-server,
    no browser headers ever attached.
"""

from __future__ import annotations

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_EXEMPT_PREFIXES = (
    "/api/v1/internal/",
    "/api/v1/mlflow-authz",
)
_ALLOWED_SEC_FETCH_SITE = frozenset({"same-origin", "none"})


def _origin_matches_host(origin: str, host: str) -> bool:
    """Return True iff Origin's scheme://host[:port] matches the request's Host.

    The Host header carries ``host[:port]`` (no scheme). We strip default
    ports (80 for http, 443 for https) before comparison so that
    ``Origin: http://example.com:80`` correctly matches ``Host: example.com``.
    Default-port forms are uncommon in browser traffic (browsers omit them
    per RFC 6454) but legitimate for some non-browser clients.
    """
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if not parsed.netloc:
        return False
    netloc = parsed.netloc
    if parsed.scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif parsed.scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    # Symmetrically strip from host if it carries the matching default port.
    if parsed.scheme == "http" and host.endswith(":80"):
        host = host[:-3]
    elif parsed.scheme == "https" and host.endswith(":443"):
        host = host[:-4]
    return netloc == host


class CSRFOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        if method not in _STATE_CHANGING_METHODS:
            return await call_next(request)

        path = request.url.path
        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Only gate /api/v1/* -- anything outside is GET-only or auth-free.
        if not path.startswith("/api/v1/"):
            return await call_next(request)

        sfs = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")

        if sfs is not None:
            if sfs not in _ALLOWED_SEC_FETCH_SITE:
                return Response(
                    content=f"csrf check failed: Sec-Fetch-Site={sfs!r}",
                    status_code=403,
                    media_type="text/plain",
                )
            return await call_next(request)

        if origin is not None:
            if not _origin_matches_host(origin, host):
                return Response(
                    content=(
                        f"csrf check failed: Origin={origin!r} does not "
                        f"match Host={host!r}"
                    ),
                    status_code=403,
                    media_type="text/plain",
                )
            return await call_next(request)

        # Both absent -- non-browser path (CLI / service token). Fail open.
        return await call_next(request)
