"""mlflow_client.MlflowClient — 2026-05-11 API surface for the MLflow redesign."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from app.services.mlflow_client import MlflowClient


@pytest.fixture
def client() -> MlflowClient:
    return MlflowClient(
        "http://mlflow.test",
        timeout=1.0,
        retries=1,
        http_client=httpx.AsyncClient(),
    )


@pytest.mark.asyncio
async def test_create_run_requires_start_time_ms(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling create_run without start_time_ms is a TypeError."""
    monkeypatch.setattr(
        client, "_request", AsyncMock(return_value={"run": {"info": {"run_id": "abc"}}})
    )
    with pytest.raises(TypeError):
        await client.create_run("42")  # type: ignore[call-arg]  # missing start_time_ms by design


@pytest.mark.asyncio
async def test_create_run_passes_start_time_in_payload(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={"run": {"info": {"run_id": "abc"}}})
    monkeypatch.setattr(client, "_request", mock)
    await client.create_run(
        "42", start_time_ms=1700000000123, tags=[{"key": "k", "value": "v"}]
    )
    kwargs = mock.call_args.kwargs
    assert kwargs["json"]["start_time"] == 1700000000123
    assert kwargs["json"]["experiment_id"] == "42"
    assert kwargs["json"]["tags"] == [{"key": "k", "value": "v"}]


@pytest.mark.asyncio
async def test_update_run_kwargs_only_with_end_time_ms(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.update_run("run-abc", status="FAILED", end_time_ms=1700000000000)
    kwargs = mock.call_args.kwargs
    assert kwargs["json"]["run_id"] == "run-abc"
    assert kwargs["json"]["status"] == "FAILED"
    assert kwargs["json"]["end_time"] == 1700000000000


@pytest.mark.asyncio
async def test_set_experiment_tag_posts_correct_payload(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.set_experiment_tag("42", "mlflow.note.content", "**Hello**")
    call = mock.call_args
    assert call.args[0] == "POST"
    assert call.args[1] == "/experiments/set-experiment-tag"
    assert call.kwargs["json"] == {
        "experiment_id": "42",
        "key": "mlflow.note.content",
        "value": "**Hello**",
    }


# ---------------------------------------------------------------------------
# from_settings classmethod (line 62 in the source). The lifespan wiring in
# app/main.py is the only caller, so a regression in the field-extraction
# (timeout / tracking URI) is otherwise invisible until production boot.
# ---------------------------------------------------------------------------


def test_from_settings_threads_uri_and_timeout() -> None:
    """Construction via the lifespan helper must pull MLFLOW_TRACKING_URI and
    MLFLOW_HTTP_TIMEOUT_SECONDS from settings; retries default to 3."""
    from types import SimpleNamespace

    settings = SimpleNamespace(
        MLFLOW_TRACKING_URI="http://mlflow.test/",
        MLFLOW_HTTP_TIMEOUT_SECONDS=7.5,
    )
    http = httpx.AsyncClient()
    c = MlflowClient.from_settings(settings, http)  # type: ignore[arg-type]
    # Trailing slash stripped per __init__.
    assert c._base == "http://mlflow.test"
    assert c._http is http
    # Timeout is wrapped in httpx.Timeout.
    assert isinstance(c._timeout, httpx.Timeout)
    assert c._retries == 3


# ---------------------------------------------------------------------------
# create_experiment with artifact_location (line 108 — the if-branch that
# threads the MinIO presigned URL into the payload).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_experiment_threads_artifact_location(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={"experiment_id": "1"})
    monkeypatch.setattr(client, "_request", mock)
    await client.create_experiment(
        "exp",
        artifact_location="s3://lolday-mlflow-artifacts/exp",
    )
    assert mock.call_args.kwargs["json"] == {
        "name": "exp",
        "artifact_location": "s3://lolday-mlflow-artifacts/exp",
    }


# ---------------------------------------------------------------------------
# get_or_create_experiment — non-RESOURCE_ALREADY_EXISTS errors must
# propagate (line 127 — the bare ``raise`` keeps the original exception so
# the caller (build_finalize) can stop the build with the actual fault).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_experiment_reraises_other_mlflow_errors(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.mlflow_client import MlflowError

    err = MlflowError("PERMISSION_DENIED: nope")
    err.code = "PERMISSION_DENIED"  # type: ignore[attr-defined]
    err.http_status = 403  # type: ignore[attr-defined]
    monkeypatch.setattr(client, "_request", AsyncMock(side_effect=err))
    with pytest.raises(MlflowError) as exc:
        await client.get_or_create_experiment("any-name")
    assert exc.value is err  # the same instance, not a re-wrapped one


# ---------------------------------------------------------------------------
# search_experiments / search_runs / set_run_tag / delete_model_version
# (lines 130-133, 168-175, 206, 241) — straight REST shape pinning.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_experiments_returns_experiments_list(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(
        return_value={
            "experiments": [
                {"experiment_id": "1", "name": "a"},
                {"experiment_id": "2", "name": "b"},
            ]
        }
    )
    monkeypatch.setattr(client, "_request", mock)
    exps = await client.search_experiments(max_results=50)
    assert [e["experiment_id"] for e in exps] == ["1", "2"]
    assert mock.call_args.kwargs["json"] == {"max_results": 50}


@pytest.mark.asyncio
async def test_search_experiments_empty_list_when_response_missing_key(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MLflow returns an empty dict when there are zero experiments — the
    helper must coerce to ``[]`` rather than raise KeyError."""
    monkeypatch.setattr(client, "_request", AsyncMock(return_value={}))
    assert await client.search_experiments() == []


@pytest.mark.asyncio
async def test_search_runs_with_filter_threads_filter_in_payload(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={"runs": [{"info": {"run_id": "r1"}}]})
    monkeypatch.setattr(client, "_request", mock)
    runs = await client.search_runs(
        ["42"], filter_string="tags.lolday.user_id = 'abc'", max_results=10
    )
    assert runs == [{"info": {"run_id": "r1"}}]
    assert mock.call_args.kwargs["json"] == {
        "experiment_ids": ["42"],
        "max_results": 10,
        "filter": "tags.lolday.user_id = 'abc'",
    }


@pytest.mark.asyncio
async def test_search_runs_omits_filter_when_none(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No filter_string -> payload must NOT carry a ``filter`` key (MLflow
    treats empty string as a literal filter expression, not absence)."""
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.search_runs(["42"])
    assert "filter" not in mock.call_args.kwargs["json"]


@pytest.mark.asyncio
async def test_set_run_tag_posts_correct_payload(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.set_run_tag("run-1", "lolday.user_id", "uuid")
    call = mock.call_args
    assert call.args == ("POST", "/runs/set-tag")
    assert call.kwargs["json"] == {
        "run_id": "run-1",
        "key": "lolday.user_id",
        "value": "uuid",
    }


@pytest.mark.asyncio
async def test_delete_model_version_uses_delete_with_json_body(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(return_value={})
    monkeypatch.setattr(client, "_request", mock)
    await client.delete_model_version("my-model", 3)
    call = mock.call_args
    assert call.args == ("DELETE", "/model-versions/delete")
    # MLflow REST contract: version is stringified at the boundary.
    assert call.kwargs["json"] == {"name": "my-model", "version": "3"}


# ---------------------------------------------------------------------------
# create_registered_model (lines 270-278) — happy path, idempotent
# RESOURCE_ALREADY_EXISTS short-circuit, and re-raise of other MlflowError.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_registered_model_happy_path(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = AsyncMock(
        return_value={
            "registered_model": {"name": "my-model", "creation_timestamp": 1700000000}
        }
    )
    monkeypatch.setattr(client, "_request", mock)
    rm = await client.create_registered_model("my-model")
    assert rm["name"] == "my-model"
    assert mock.call_args.kwargs["json"] == {"name": "my-model"}


@pytest.mark.asyncio
async def test_create_registered_model_idempotent_on_already_exists(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second caller (parallel build) gets ``RESOURCE_ALREADY_EXISTS``; the
    helper must short-circuit to the canonical ``{"name": ...}`` shape so
    the caller cannot tell the difference from a fresh-create response."""
    from app.services.mlflow_client import MlflowError

    err = MlflowError("RESOURCE_ALREADY_EXISTS: exists")
    err.code = "RESOURCE_ALREADY_EXISTS"  # type: ignore[attr-defined]
    err.http_status = 400  # type: ignore[attr-defined]
    monkeypatch.setattr(client, "_request", AsyncMock(side_effect=err))
    rm = await client.create_registered_model("my-model")
    assert rm == {"name": "my-model"}


@pytest.mark.asyncio
async def test_create_registered_model_reraises_other_errors(
    client: MlflowClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-RESOURCE_ALREADY_EXISTS MlflowError must propagate so the caller
    fails the build / 5xx — silencing it would let the build claim success
    without an MLflow model row backing it."""
    from app.services.mlflow_client import MlflowError

    err = MlflowError("PERMISSION_DENIED: nope")
    err.code = "PERMISSION_DENIED"  # type: ignore[attr-defined]
    err.http_status = 403  # type: ignore[attr-defined]
    monkeypatch.setattr(client, "_request", AsyncMock(side_effect=err))
    with pytest.raises(MlflowError) as exc:
        await client.create_registered_model("my-model")
    assert exc.value is err


# ---------------------------------------------------------------------------
# _request non-JSON 4xx body fallback (lines 85-86). MLflow's REST normally
# returns JSON for errors, but the upstream proxy / sidecar can interject
# an HTML / plaintext body (502 from Traefik, etc.). The fallback path
# constructs a synthetic error dict so _handle_error doesn't itself raise
# AttributeError on missing keys.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_non_json_4xx_body_falls_back_to_unknown_code(
    client: MlflowClient,
) -> None:
    import respx
    from app.services.mlflow_client import MlflowError

    with respx.mock(base_url="http://mlflow.test") as mock:
        mock.post("/api/2.0/mlflow/experiments/create").mock(
            return_value=httpx.Response(
                502,
                content=b"<html><body>Bad Gateway</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        with pytest.raises(MlflowError) as exc:
            await client.create_experiment("any")
    # The fallback synthesises error_code=UNKNOWN with the raw body as message.
    assert "UNKNOWN" in str(exc.value)
    assert exc.value.code == "UNKNOWN"  # type: ignore[attr-defined]
    assert exc.value.http_status == 502  # type: ignore[attr-defined]
