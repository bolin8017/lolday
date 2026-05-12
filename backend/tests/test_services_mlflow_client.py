import contextlib

import httpx
import pytest
import respx
from app.services import mlflow_client as mlflow_client_mod
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


@pytest.fixture(autouse=True)
async def _reset_mlflow_http_client():
    """Reset the module-level singleton between tests so each test sees a
    fresh AsyncClient. The fix introduces a shared AsyncClient that
    otherwise lives across the whole test session and confuses
    construction-count assertions and respx scoping."""
    mlflow_client_mod._HTTP_CLIENT = None
    yield
    if mlflow_client_mod._HTTP_CLIENT is not None:
        with contextlib.suppress(Exception):
            await mlflow_client_mod.close_http_client()


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
            json={
                "error_code": "RESOURCE_ALREADY_EXISTS",
                "message": "experiment exists",
            },
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
    rid = await c.create_run("42", start_time_ms=1700000000000)
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
async def test_search_model_versions_uses_get_with_query_params():
    """MLflow 2.20 /api/2.0/mlflow/model-versions/search is GET-only; POST returns 405.

    Regression guard: earlier code sent POST with a JSON body and silently raised
    MlflowError("UNKNOWN: 405 Method Not Allowed") on every reconciler iteration.
    """
    route = respx.get("http://mlflow/api/2.0/mlflow/model-versions/search").mock(
        return_value=httpx.Response(200, json=MODEL_VERSIONS_SEARCH)
    )
    c = MlflowClient("http://mlflow")
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


@pytest.mark.asyncio
@respx.mock
async def test_rename_registered_model():
    """Rename POST hits the right endpoint and returns the registered_model dict."""
    route = respx.post("http://mlflow/api/2.0/mlflow/registered-models/rename").mock(
        return_value=httpx.Response(200, json=REGISTERED_MODEL_RENAMED)
    )
    c = MlflowClient("http://mlflow")
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
    c = MlflowClient("http://mlflow")
    result = await c.delete_registered_model("upxelfdet")
    assert route.called
    sent = route.calls.last.request
    assert sent.method == "DELETE"
    body = sent.content.decode("utf-8")
    assert "upxelfdet" in body
    assert result is None


# ============================================================================
# Regression tests for the 2026-05-12 AsyncClient-leak fix
# (spec: docs/superpowers/specs/2026-05-12-mlflow-client-async-leak-fix-design.md)
# ============================================================================


@pytest.mark.asyncio
@respx.mock
async def test_request_reuses_module_level_async_client(monkeypatch):
    """Regression for the ~0.9 MiB/min residual leak observed in v0.21.1.

    Pre-fix code did ``async with httpx.AsyncClient(timeout=...) as client:``
    inside ``_request`` on every call.  sync_model_versions fires this
    every 60 s, churning ~0.8 MiB of glibc arena pages per construction.
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

    c = MlflowClient("http://mlflow")
    for _ in range(10):
        await c.get_experiment_by_name("any")

    assert construction_count == 1, (
        f"_request must reuse a module-level AsyncClient (lazy-init once); "
        f"saw {construction_count} new AsyncClient constructions across 10 calls."
    )


@pytest.mark.asyncio
async def test_module_level_http_client_lazy_init_on_first_use():
    """Module-load does NOT construct the AsyncClient (no event loop yet)."""
    # The autouse fixture has already reset _HTTP_CLIENT to None at test
    # entry.  Importing mlflow_client must not have created a Client.
    assert mlflow_client_mod._HTTP_CLIENT is None


@pytest.mark.asyncio
@respx.mock
async def test_close_http_client_is_idempotent():
    """Calling close_http_client() twice must not raise.

    Lifespan teardown does not guard against double-close; a regression
    that drops the ``if _HTTP_CLIENT is None: return`` early-exit would
    AttributeError on the second call.
    """
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json={"experiment": {"experiment_id": "1"}})
    )
    c = MlflowClient("http://mlflow")
    await c.get_experiment_by_name("any")  # lazy-init the client

    assert mlflow_client_mod._HTTP_CLIENT is not None
    await mlflow_client_mod.close_http_client()
    assert mlflow_client_mod._HTTP_CLIENT is None

    # Second call: no-op, no raise.
    await mlflow_client_mod.close_http_client()
    assert mlflow_client_mod._HTTP_CLIENT is None


@pytest.mark.asyncio
async def test_close_http_client_swallows_aclose_exceptions(monkeypatch):
    """close_http_client() must not propagate aclose() exceptions.

    Lifespan teardown propagating a transport error would abort the
    hygiene step for everything wired after it.  Mirrors the gpu_signal
    contract; reference is nulled *before* aclose() so the post-close
    invariant holds even when the underlying close fails.
    """
    from unittest.mock import AsyncMock, MagicMock

    fake_client = MagicMock()
    fake_client.aclose = AsyncMock(side_effect=OSError("transport already gone"))
    monkeypatch.setattr(mlflow_client_mod, "_HTTP_CLIENT", fake_client)

    await mlflow_client_mod.close_http_client()  # must not raise

    assert mlflow_client_mod._HTTP_CLIENT is None
    fake_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@respx.mock
async def test_request_after_close_recreates_client():
    """After close_http_client(), the next _request lazily recreates the Client.

    Unlike gpu_signal (which raises PrometheusUnavailable after close),
    mlflow_client's lazy-init is the same call site for "first use" and
    "post-close use" — neither has special meaning. A test runs both
    pre- and post-close to validate this is intentional.
    """
    respx.get("http://mlflow/api/2.0/mlflow/experiments/get-by-name").mock(
        return_value=httpx.Response(200, json={"experiment": {"experiment_id": "1"}})
    )
    c = MlflowClient("http://mlflow")
    await c.get_experiment_by_name("any")  # 1st client lazy-init
    first_client = mlflow_client_mod._HTTP_CLIENT
    assert first_client is not None

    await mlflow_client_mod.close_http_client()
    assert mlflow_client_mod._HTTP_CLIENT is None

    await c.get_experiment_by_name("any")  # recreates 2nd client
    second_client = mlflow_client_mod._HTTP_CLIENT
    assert second_client is not None
    assert second_client is not first_client
