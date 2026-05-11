"""mlflow_client.MlflowClient — 2026-05-11 API surface for the MLflow redesign."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.services.mlflow_client import MlflowClient


@pytest.fixture
def client() -> MlflowClient:
    return MlflowClient("http://mlflow.test", timeout=1.0, retries=1)


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
