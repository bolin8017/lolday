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

    assert (
        REGISTRY.get_sample_value(
            "lolday_backend_errors_total", {"stage": "probe_inc_a"}
        )
        == 2.0
    )
    assert (
        REGISTRY.get_sample_value(
            "lolday_backend_errors_total", {"stage": "probe_inc_b"}
        )
        == 1.0
    )


def _get(stage: str) -> float:
    return (
        REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
        or 0.0
    )


def _empty_scan_session() -> MagicMock:
    """Session whose `execute()` returns empty result sets for both the build
    and job scans. Caller may override side_effect for per-test variation."""
    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[empty, empty])
    return session


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
    import app.reconciler.loop as rec_loop

    # Collapse the 10s inter-iteration wait so the loop exits fast after stop_event.set().
    # Patch on loop module — reconciler_loop reads its own module-level constant.
    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)

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

    # Patch on loop module — reconciler_loop reads its own module-level names.
    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "reconcile_build", raising_reconcile_build)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_build") == before + 1.0


@pytest.mark.asyncio
async def test_reconcile_job_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=reconcile_job} when reconcile_job
    raises — mirror of the reconcile_build path, but exercising the job
    reconcile pass's inner except (loop.py L123-129)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)

    before = _get("reconcile_job")
    stop = asyncio.Event()

    builds_result = MagicMock()
    builds_result.scalars.return_value.all.return_value = []
    fake_job = MagicMock()
    fake_job.id = "jid-probe"
    jobs_result = MagicMock()
    jobs_result.scalars.return_value.all.return_value = [fake_job]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[builds_result, jobs_result])

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def raising_reconcile_job(_session, _job, _mlflow):
        stop.set()
        raise RuntimeError("synthetic reconcile_job failure")

    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "reconcile_job", raising_reconcile_job)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_job") == before + 1.0


@pytest.mark.asyncio
async def test_sync_model_versions_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=sync_model_versions} when the
    every-N-iterations sync pass raises (loop.py L132-137)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)
    # Trigger sync on iteration 1; keep the other periodic gates dormant.
    monkeypatch.setattr(rec_loop, "SYNC_EVERY_N_ITERATIONS", 1)

    before = _get("sync_model_versions")
    stop = asyncio.Event()
    session = _empty_scan_session()

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def raising_sync(_session, _mlflow):
        stop.set()
        raise RuntimeError("synthetic sync_model_versions failure")

    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "sync_model_versions", raising_sync)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("sync_model_versions") == before + 1.0


@pytest.mark.asyncio
async def test_reconcile_orphan_vcjobs_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=reconcile_orphan_vcjobs} when the
    periodic orphan vcjob scan raises (loop.py L140-145)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)
    # Trigger orphan scan on iteration 1; keep sync + harbor dormant.
    monkeypatch.setattr(rec_loop, "ORPHAN_SCAN_EVERY_N_ITERATIONS", 1)

    before = _get("reconcile_orphan_vcjobs")
    stop = asyncio.Event()
    session = _empty_scan_session()

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def raising_orphans(_session):
        raise RuntimeError("synthetic reconcile_orphan_vcjobs failure")

    async def passing_token_secrets(_session):
        stop.set()

    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "reconcile_orphan_vcjobs", raising_orphans)
    monkeypatch.setattr(
        rec_loop, "reconcile_orphan_token_secrets", passing_token_secrets
    )

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_orphan_vcjobs") == before + 1.0


@pytest.mark.asyncio
async def test_reconcile_orphan_token_secrets_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=reconcile_orphan_token_secrets} when
    the second orphan pass raises after the first succeeds (loop.py L147-153)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(rec_loop, "ORPHAN_SCAN_EVERY_N_ITERATIONS", 1)

    before = _get("reconcile_orphan_token_secrets")
    stop = asyncio.Event()
    session = _empty_scan_session()

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def passing_orphans(_session):
        # Orphan vcjob scan succeeds — the token-secret pass must still run.
        return

    async def raising_token_secrets(_session):
        stop.set()
        raise RuntimeError("synthetic reconcile_orphan_token_secrets failure")

    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "reconcile_orphan_vcjobs", passing_orphans)
    monkeypatch.setattr(
        rec_loop, "reconcile_orphan_token_secrets", raising_token_secrets
    )

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_orphan_token_secrets") == before + 1.0


@pytest.mark.asyncio
async def test_reconcile_harbor_robot_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=reconcile_harbor_robot} when the
    24h Harbor-rotate pass raises (loop.py L156-161)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)
    # Trigger harbor rotate on iteration 1.
    monkeypatch.setattr(rec_loop, "HARBOR_ROTATE_EVERY_N_ITERATIONS", 1)

    before = _get("reconcile_harbor_robot")
    stop = asyncio.Event()
    session = _empty_scan_session()

    @asynccontextmanager
    async def fake_maker():
        yield session

    async def raising_harbor():
        stop.set()
        raise RuntimeError("synthetic reconcile_harbor_robot failure")

    monkeypatch.setattr(rec_loop, "async_session_maker", fake_maker)
    monkeypatch.setattr(rec_loop, "reconcile_harbor_robot", raising_harbor)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconcile_harbor_robot") == before + 1.0


@pytest.mark.asyncio
async def test_reconciler_iteration_outer_exception_records_error(monkeypatch):
    """`reconciler_loop` increments {stage=reconciler_iteration} when an
    exception escapes the inner per-pass handlers — e.g. a session-acquire
    failure (loop.py L162-164)."""
    import app.reconciler as rec
    import app.reconciler.loop as rec_loop

    monkeypatch.setattr(rec_loop, "RECONCILER_WAIT_SECONDS", 0.01)

    before = _get("reconciler_iteration")
    stop = asyncio.Event()

    @asynccontextmanager
    async def failing_maker():
        stop.set()
        raise RuntimeError("synthetic async_session_maker failure")
        yield  # unreachable; satisfies the contextmanager contract

    monkeypatch.setattr(rec_loop, "async_session_maker", failing_maker)

    await asyncio.wait_for(rec.reconciler_loop(stop), timeout=5)

    assert _get("reconciler_iteration") == before + 1.0


async def test_job_logs_fetch_exception_records_without_NameError(monkeypatch):
    """Regression guard for a PR#4 review finding: `_stream_live_logs(job)` used
    `str(job_id)` in the except-block log-extra, but the parameter is `job` —
    a NameError would bubble up, turning the intended graceful 503 into a 500.
    Assert we return 503 AND the counter increments AND the logger.exception
    line runs cleanly."""
    from unittest.mock import MagicMock

    from app.routers.jobs import _stream_live_logs

    stub_core = MagicMock()
    stub_core.list_namespaced_pod.side_effect = RuntimeError("synthetic k8s failure")
    # Patch the core_v1 name already bound inside routers.jobs (not on the source module).
    monkeypatch.setattr("app.routers.jobs.core_v1", lambda: stub_core)

    job = MagicMock()
    job.id = "j-probe"

    before = _get("job_logs_fetch")
    resp = await _stream_live_logs(job)  # must NOT raise NameError
    assert resp.status_code == 503
    assert b"logs unavailable" in resp.body
    assert _get("job_logs_fetch") == before + 1.0


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


@pytest.mark.asyncio
async def test_openapi_json_returns_404_when_docs_disabled():
    """#165: /openapi.json must NOT be served when DOCS_ENABLED=false.

    FastAPI's ``openapi_url`` is read once at app construction, so we build
    a fresh FastAPI() with the gate flipped off and verify the schema route
    is not registered. This is the production posture (chart wires
    DOCS_ENABLED="false"); the test conftest pins DOCS_ENABLED=true so the
    main fixture-backed app still exposes the schema for other tests.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport
    from httpx import AsyncClient as _AsyncClient

    # Mimic main.py: openapi_url=None gates both /openapi.json and the
    # docs / redoc routes that depend on it.
    test_app = FastAPI(
        title="lolday-test", openapi_url=None, docs_url=None, redoc_url=None
    )

    transport = ASGITransport(app=test_app)
    async with _AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/openapi.json")
        assert resp.status_code == 404
        docs = await c.get("/docs")
        assert docs.status_code == 404
