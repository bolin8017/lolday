"""Phase 6: verify /metrics endpoint is exposed for Prometheus scraping."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_endpoint_exists(client: AsyncClient):
    """Metrics endpoint must be publicly reachable inside the cluster."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_content_is_prometheus_format(client: AsyncClient):
    """Content-Type and body must be Prometheus text exposition format."""
    resp = await client.get("/metrics")
    ctype = resp.headers.get("content-type", "")
    # prometheus_client uses text/plain; version=0.0.4
    assert ctype.startswith("text/plain")
    body = resp.text
    # Every Prometheus exposition has at least one HELP/TYPE block
    assert "# HELP" in body
    assert "# TYPE" in body


@pytest.mark.asyncio
async def test_metrics_includes_http_counter(client: AsyncClient):
    """The default instrumentator emits http_requests_total after any request."""
    # Generate at least one request so a counter exists
    await client.get("/api/v1/health")
    resp = await client.get("/metrics")
    # prometheus-fastapi-instrumentator default metric name
    assert "http_requests_total" in resp.text


@pytest.mark.asyncio
async def test_metrics_not_in_openapi_schema(client: AsyncClient):
    """/metrics must NOT appear in OpenAPI — it's an internal endpoint."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/metrics" not in paths
