"""Unit test for ``app.internal_app.livez``.

The `livez` endpoint is the only locally-defined route on
``internal_app`` (separate FastAPI app bound to :8001 by the
entrypoint). It is what the K8s livenessProbe on the backend pod hits;
a 5xx here means the pod gets restarted by the kubelet.

Coverage on `app/internal_app.py` was stuck at 86% because the route
body itself (the ``return {"status": "ok"}``) was never invoked under
pytest — every router test mounts a fresh FastAPI for the public app on
:8000, not the internal_app.
"""

from __future__ import annotations

from starlette.testclient import TestClient


def test_internal_app_livez_returns_200_ok() -> None:
    from app.internal_app import internal_app

    client = TestClient(internal_app)
    r = client.get("/livez")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_internal_app_livez_is_excluded_from_openapi_schema() -> None:
    """``include_in_schema=False`` keeps the K8s probe endpoint out of
    the public OpenAPI surface. Without this assertion a future
    refactor that drops the flag would silently widen the public API
    contract (and surface a noisy schemathesis test that doesn't model
    a livenessProbe endpoint)."""
    from app.internal_app import internal_app

    paths = (internal_app.openapi() or {}).get("paths", {})
    assert "/livez" not in paths
