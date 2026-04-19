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
    REGISTERED_MODELS_SEARCH,
    RUN_CREATED,
    RUN_FINISHED,
)


@pytest.mark.asyncio
@respx.mock
async def test_create_experiment_returns_id():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_CREATED)
    )
    c = MlflowClient("http://mlflow")
    eid = await c.create_experiment("my-exp", artifact_location=None)
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_get_or_create_experiment_reuses_existing():
    """If creating returns 'RESOURCE_ALREADY_EXISTS', fall back to get-by-name."""
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(
            400,
            json={"error_code": "RESOURCE_ALREADY_EXISTS", "message": "experiment exists"},
        )
    )
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json=EXPERIMENT_GET)
    )
    c = MlflowClient("http://mlflow")
    eid = await c.get_or_create_experiment("detector:upxelfdet:v0.4.0")
    assert eid == "42"


@pytest.mark.asyncio
@respx.mock
async def test_create_run_returns_run_id():
    respx.post("http://mlflow/api/2.0/mlflow/runs/create").mock(
        return_value=httpx.Response(200, json=RUN_CREATED)
    )
    c = MlflowClient("http://mlflow")
    rid = await c.create_run("42")
    assert rid == "abc123def456"


@pytest.mark.asyncio
@respx.mock
async def test_get_run_parses_metrics():
    respx.get("http://mlflow/api/2.0/mlflow/runs/get").mock(
        return_value=httpx.Response(200, json=RUN_FINISHED)
    )
    c = MlflowClient("http://mlflow")
    run = await c.get_run("abc123def456")
    assert run["info"]["status"] == "FINISHED"
    assert run["data"]["metrics"][0]["key"] == "accuracy"


@pytest.mark.asyncio
@respx.mock
async def test_create_model_version_returns_version():
    respx.post("http://mlflow/api/2.0/mlflow/model-versions/create").mock(
        return_value=httpx.Response(200, json=MODEL_VERSION_CREATED)
    )
    c = MlflowClient("http://mlflow")
    mv = await c.create_model_version("upxelfdet", "runs:/abc123def456/model", "abc123def456")
    assert mv["version"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_transition_stage_calls_correct_endpoint():
    route = respx.post("http://mlflow/api/2.0/mlflow/model-versions/transition-stage").mock(
        return_value=httpx.Response(200, json=MODEL_VERSION_TRANSITIONED)
    )
    c = MlflowClient("http://mlflow")
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
    c = MlflowClient("http://mlflow")
    models = await c.search_registered_models()
    assert models[0]["name"] == "upxelfdet"


@pytest.mark.asyncio
@respx.mock
async def test_search_model_versions():
    respx.post("http://mlflow/api/2.0/mlflow/model-versions/search").mock(
        return_value=httpx.Response(200, json=MODEL_VERSIONS_SEARCH)
    )
    c = MlflowClient("http://mlflow")
    versions = await c.search_model_versions(filter_string="name = 'upxelfdet'")
    assert len(versions) == 1


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_mlflow_error():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        return_value=httpx.Response(500, json={"error_code": "INTERNAL_ERROR", "message": "boom"})
    )
    c = MlflowClient("http://mlflow")
    with pytest.raises(MlflowError, match="INTERNAL_ERROR"):
        await c.create_experiment("any")


@pytest.mark.asyncio
@respx.mock
async def test_network_timeout_retries_then_raises():
    respx.post("http://mlflow/api/2.0/mlflow/experiments/create").mock(
        side_effect=httpx.ConnectError("conn refused")
    )
    c = MlflowClient("http://mlflow", timeout=0.1, retries=2)
    with pytest.raises(MlflowError, match="network"):
        await c.create_experiment("any")
