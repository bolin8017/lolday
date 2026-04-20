"""Phase 6: verify /metrics endpoint is exposed for Prometheus scraping.

Also covers the `lolday_backend_errors_total` custom Counter added post-phase6
to make silent-failure paths observable (see reconciler.py + harbor_init.py).
"""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY


@pytest.mark.asyncio
async def test_metrics_endpoint_exists(client: AsyncClient):
    """Metrics endpoint must be publicly reachable inside the cluster."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200


def test_backend_errors_counter_registered_with_stage_label():
    """`lolday_backend_errors_total` Counter is registered on module import; child metric with `stage` label starts at 0."""
    from app.metrics import BACKEND_ERRORS

    BACKEND_ERRORS.labels(stage="probe_registered_x7")
    value = REGISTRY.get_sample_value(
        "lolday_backend_errors_total",
        {"stage": "probe_registered_x7"},
    )
    assert value == 0.0


def test_backend_errors_counter_increments_per_stage():
    """Counter increments independently per `stage` label value."""
    from app.metrics import BACKEND_ERRORS

    BACKEND_ERRORS.labels(stage="probe_inc_a").inc()
    BACKEND_ERRORS.labels(stage="probe_inc_a").inc()
    BACKEND_ERRORS.labels(stage="probe_inc_b").inc()

    assert REGISTRY.get_sample_value(
        "lolday_backend_errors_total", {"stage": "probe_inc_a"}
    ) == 2.0
    assert REGISTRY.get_sample_value(
        "lolday_backend_errors_total", {"stage": "probe_inc_b"}
    ) == 1.0


def _get(stage: str) -> float:
    return REGISTRY.get_sample_value(
        "lolday_backend_errors_total", {"stage": stage}
    ) or 0.0


@pytest.mark.asyncio
async def test_reconcile_build_exception_records_error(monkeypatch):
    """Integration smoke: reconciler_loop increments {stage=reconcile_build} when reconcile_build raises.

    Proves the counter is wired into the outer except at L224. The other four
    reconciler sites (L234/L241/L243/L354) and the harbor_init sites follow
    the identical two-line pattern (`BACKEND_ERRORS.labels(stage=...).inc()`
    immediately before `logger.exception(...)`), verified by code review +
    post-deploy /metrics smoke test.
    """
    import app.reconciler as rec

    # Collapse the 10s inter-iteration wait so the loop exits fast after stop_event.set().
    monkeypatch.setattr(rec, "RECONCILER_WAIT_SECONDS", 0.01)

    before = _get("reconcile_build")
    stop = asyncio.Event()

    fake_build = MagicMock()
    fake_build.id = "bid-probe"
    builds_result = MagicMock()
    builds_result.scalars.return_value.all.return_value = [fake_build]
    jobs_result = MagicMock()
    jobs_result.scalars.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[builds_result, jobs_result])

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def raising_reconcile_build(_session, _build):
        stop.set()
        raise RuntimeError("synthetic reconcile_build failure")

    monkeypatch.setattr(rec, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec, "reconcile_build", raising_reconcile_build)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_build") == before + 1.0


@pytest.mark.asyncio
async def test_metrics_content_is_prometheus_format(client: AsyncClient):
    """Content-Type and body must be Prometheus text exposition format."""
    resp = await client.get("/metrics")
    ctype = resp.headers.get("content-type", "")
    assert ctype.startswith("text/plain")
    body = resp.text
    assert "# HELP" in body
    assert "# TYPE" in body


@pytest.mark.asyncio
async def test_metrics_includes_http_counter(client: AsyncClient):
    """The default instrumentator emits http_requests_total after any request."""
    await client.get("/api/v1/health")
    resp = await client.get("/metrics")
    assert "http_requests_total" in resp.text


@pytest.mark.asyncio
async def test_metrics_includes_backend_errors_series(client: AsyncClient):
    """After BACKEND_ERRORS is touched, the series must appear on /metrics."""
    from app.metrics import BACKEND_ERRORS
    BACKEND_ERRORS.labels(stage="probe_exposed").inc()
    resp = await client.get("/metrics")
    assert "lolday_backend_errors_total" in resp.text
    assert 'stage="probe_exposed"' in resp.text


@pytest.mark.asyncio
async def test_metrics_not_in_openapi_schema(client: AsyncClient):
    """/metrics must NOT appear in OpenAPI — it's an internal endpoint."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/metrics" not in paths
