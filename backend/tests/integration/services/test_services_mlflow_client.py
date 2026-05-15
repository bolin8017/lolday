import httpx
import pytest
import respx
from app.services.mlflow_client import MlflowClient, MlflowError

from tests.fixtures.sample_mlflow_responses import (
    EXPERIMENT_CREATED,
    EXPERIMENT_GET,
    MODEL_VERSION_CREATED,
    MODEL_VERSION_TRANSITIONED,
    MODEL_VERSIONS_SEARCH,
    REGISTERED_MODEL_RENAMED,
    REGISTERED_MODELS_SEARCH,
    RUN_CREATED,
    RUN_FINISHED,
)


def _client(**kwargs) -> MlflowClient:
    """Construct a test MlflowClient with a fresh AsyncClient."""
    return MlflowClient(
        "http://mlflow",
        http_client=httpx.AsyncClient(),
        **kwargs,
    )


@pytest.mark.asyncio
@respx.mock
async def test_create_experiment_returns_id():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_CREATED)
    )
    c = _client()
    eid = await c.create_experiment("my-exp", artifact_location=None)
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_get_or_create_experiment_reuses_existing():
    """If creating returns 'RESOURCE_ALREADY_EXISTS', fall back to get-by-name."""
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(
            400,
            json={
                "error_code": "RESOURCE_ALREADY_EXISTS",
                "message": "experiment exists",
            },
        )
    )
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_GET)
    )
    c = _client()
    eid = await c.get_or_create_experiment("detector:upxelfdet:v0.4.0")
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_create_run_returns_run_id():
    respx.post("http://mlflow/api/2.0/mlflow/runs/create").mock(
        return_value=httpx.Response(200, json=RUN_CREATED)
    )
    c = _client()
    rid = await c.create_run("42", start_time_ms=1700000000000)
    assert rid == "abc123def456"


@pytest.mark.asyncio
@respx.mock
async def test_get_run_parses_metrics():
    respx.get("http://mlflow/api/2.0/mlflow/runs/get").mock(
        return_value=httpx.Response(200, json=RUN_FINISHED)
    )
    c = _client()
    run = await c.get_run("abc123def456")
    assert run["info"]["status"] == "FINISHED"
    assert run["data"]["metrics"][0]["key"] == "accuracy"


@pytest.mark.asyncio
@respx.mock
async def test_create_model_version_returns_version():
    respx.post("http://mlflow/api/2.0/mlflow/model-versions/create").mock(
        return_value=httpx.Response(200, json=MODEL_VERSION_CREATED)
    )
    c = _client()
    mv = await c.create_model_version(
        "upxelfdet", "runs:/abc123def456/model", "abc123def456"
    )
    assert mv["version"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_transition_stage_calls_correct_endpoint():
    route = respx.post(
        "http://mlflow/api/2.0/mlflow/model-versions/transition-stage"
    ).mock(return_value=httpx.Response(200, json=MODEL_VERSION_TRANSITIONED))
    c = _client()
    mv = await c.transition_model_version_stage(
        "upxelfdet", "1", "Production", archive_existing_versions=True
    )
    assert route.called
    sent = route.calls.last.request
    body = sent.content.decode("utf-8")
    assert "Production" in body
    assert "archive_existing_versions" in body
    assert mv["current_stage"] == "Production"


@pytest.mark.asyncio
@respx.mock
async def test_search_registered_models_paginates():
    respx.get("http://mlflow/api/2.0/mlflow/registered-models/search").mock(
        return_value=httpx.Response(200, json=REGISTERED_MODELS_SEARCH)
    )
    c = _client()
    models = await c.search_registered_models()
    assert models[0]["name"] == "upxelfdet"


@pytest.mark.asyncio
@respx.mock
async def test_search_model_versions_uses_get_with_query_params():
    """MLflow 2.20 /api/2.0/mlflow/model-versions/search is GET-only; POST returns 405.

    Regression guard: earlier code sent POST with a JSON body and silently raised
    MlflowError("UNKNOWN: 405 Method Not Allowed") on every reconciler iteration.
    """
    route = respx.get("http://mlflow/api/2.0/mlflow/model-versions/search").mock(
        return_value=httpx.Response(200, json=MODEL_VERSIONS_SEARCH)
    )
    c = _client()
    versions = await c.search_model_versions(
        filter_string="name = 'upxelfdet'", max_results=200
    )

    assert route.called
    sent = route.calls.last.request
    assert sent.method == "GET"
    qs = sent.url.params
    assert qs["filter"] == "name = 'upxelfdet'"
    assert qs["max_results"] == "200"
    assert sent.read() == b""  # no body on GET
    assert len(versions) == 1


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_mlflow_error():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(
            500, json={"error_code": "INTERNAL_ERROR", "message": "boom"}
        )
    )
    c = _client()
    with pytest.raises(MlflowError, match="INTERNAL_ERROR"):
        await c.create_experiment("any")


@pytest.mark.asyncio
@respx.mock
async def test_network_timeout_retries_then_raises():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        side_effect=httpx.ConnectError("conn refused")
    )
    c = MlflowClient(
        "http://mlflow",
        timeout=0.1,
        retries=2,
        http_client=httpx.AsyncClient(),
    )
    with pytest.raises(MlflowError, match="network"):
        await c.create_experiment("any")


@pytest.mark.asyncio
@respx.mock
async def test_rename_registered_model():
    """Rename POST hits the right endpoint and returns the registered_model dict."""
    route = respx.post("http://mlflow/api/2.0/mlflow/registered-models/rename").mock(
        return_value=httpx.Response(200, json=REGISTERED_MODEL_RENAMED)
    )
    c = _client()
    model = await c.rename_registered_model("upxelfdet", "alice:upxelfdet")
    assert route.called
    sent = route.calls.last.request
    body = sent.content.decode("utf-8")
    assert "alice:upxelfdet" in body
    assert model["name"] == "alice:upxelfdet"


@pytest.mark.asyncio
@respx.mock
async def test_delete_registered_model():
    """Delete uses DELETE and hits the right endpoint; returns None."""
    route = respx.delete("http://mlflow/api/2.0/mlflow/registered-models/delete").mock(
        return_value=httpx.Response(200, json={})
    )
    c = _client()
    result = await c.delete_registered_model("upxelfdet")
    assert route.called
    sent = route.calls.last.request
    assert sent.method == "DELETE"
    body = sent.content.decode("utf-8")
    assert "upxelfdet" in body
    assert result is None


# ============================================================================
# Regression test for the 2026-05-12 AsyncClient-leak fix
# (spec: docs/superpowers/specs/2026-05-12-mlflow-client-async-leak-fix-design.md)
#
# After the T13 shim removal the client is always injected by the caller
# (lifespan-owned in production, test-local here). The invariant being
# guarded: _request must NOT create a new AsyncClient per call; it must
# reuse the one passed at construction time.
# ============================================================================


@pytest.mark.asyncio
@respx.mock
async def test_request_reuses_injected_async_client(monkeypatch):
    """Regression: _request must reuse the injected AsyncClient, not construct new ones.

    Pre-T13 code did ``async with httpx.AsyncClient(timeout=...) as client:``
    inside ``_request`` on every call.  sync_model_versions fires this
    every 60 s, churning ~0.8 MiB of glibc arena pages per construction.
    After T13 the injected client is stored in ``self._http`` and reused;
    no new AsyncClient should be constructed across 10 calls.
    """
    construction_count = 0
    real_init = httpx.AsyncClient.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal construction_count
        construction_count += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json={"experiment": {"experiment_id": "1"}})
    )

    # Construction count starts at 1 (the client we pass in).
    shared_client = httpx.AsyncClient()
    c = MlflowClient("http://mlflow", http_client=shared_client)
    for _ in range(10):
        await c.get_experiment_by_name("any")

    assert construction_count == 1, (
        f"_request must reuse the injected AsyncClient (no new constructions during "
        f"10 calls); saw {construction_count} new AsyncClient constructions total."
    )
