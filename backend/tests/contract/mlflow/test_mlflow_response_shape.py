"""Contract test against the MLflow REST API shape.

Uses respx to mock the network layer with previously-recorded responses
in backend/tests/fixtures/mlflow/recorded/. Fails if our MlflowClient
class is unable to parse the recorded shapes — a signal that the MLflow
REST contract has drifted.

To re-record after an MLflow version bump:
    1. Start MLflow via Docker:
           docker run --rm -d --name mlflow-record -p 15000:5000 \\
             ghcr.io/mlflow/mlflow:v<NEW> \\
             mlflow server --host 0.0.0.0 --port 5000 \\
               --backend-store-uri sqlite:////tmp/mlflow.db \\
               --default-artifact-root /tmp/artifacts
    2. Wait for readiness (curl -fs http://localhost:15000/health).
    3. Run the curl sequence:
           cd backend/tests/fixtures/mlflow/recorded/
           curl -sX POST http://localhost:15000/api/2.0/mlflow/experiments/create \\
             -H 'Content-Type: application/json' \\
             -d '{"name":"test-experiment-tape"}' | tee create_experiment.json
           EXP_ID=$(python3 -c "import json; print(json.load(open('create_experiment.json'))['experiment_id'])")
           NOW_MS=$(python3 -c "import time; print(int(time.time()*1000))")
           curl -sX POST http://localhost:15000/api/2.0/mlflow/runs/create \\
             -H 'Content-Type: application/json' \\
             -d "{\"experiment_id\":\"$EXP_ID\",\"start_time\":$NOW_MS}" | tee create_run.json
           RUN_ID=$(python3 -c "import json; print(json.load(open('create_run.json'))['run']['info']['run_id'])")
           curl -s "http://localhost:15000/api/2.0/mlflow/runs/get?run_id=$RUN_ID" | tee get_run.json
           curl -sX POST http://localhost:15000/api/2.0/mlflow/runs/update \\
             -H 'Content-Type: application/json' \\
             -d "{\"run_id\":\"$RUN_ID\",\"status\":\"FINISHED\",\"end_time\":1700000000000}" \\
             | tee terminate_run.json
           docker rm -f mlflow-record
    4. Re-stage the four JSON files and commit with a message noting the version delta.

Recorded against: ghcr.io/mlflow/mlflow:v3.11.1
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from app.services.mlflow_client import MlflowClient

pytestmark = pytest.mark.contract

TAPE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "mlflow" / "recorded"


@pytest.fixture
def make_mlflow_client():
    """Return a factory that builds a MlflowClient backed by a fresh AsyncClient.

    respx intercepts all outbound requests; the base_url value is arbitrary
    but must match what the client would send.
    """

    async def _factory() -> MlflowClient:
        http = httpx.AsyncClient(base_url="http://mlflow.test")
        return MlflowClient(
            "http://mlflow.test",
            http_client=http,
        )

    return _factory


@pytest.mark.asyncio
async def test_create_experiment_parses_recorded_response(make_mlflow_client):
    """create_experiment should extract experiment_id from the recorded shape."""
    with open(TAPE_DIR / "create_experiment.json") as f:
        recorded = json.load(f)

    with respx.mock() as mock:
        mock.post("http://mlflow.test/api/2.0/mlflow/experiments/create").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        client = await make_mlflow_client()
        try:
            exp_id = await client.create_experiment(name="test-experiment-tape")
        finally:
            await client._http.aclose()

    # MlflowClient.create_experiment returns the experiment_id string directly.
    assert isinstance(exp_id, str)
    assert exp_id  # non-empty


@pytest.mark.asyncio
async def test_create_run_parses_recorded_response(make_mlflow_client):
    """create_run should extract run_id from the recorded shape."""
    with open(TAPE_DIR / "create_run.json") as f:
        recorded = json.load(f)

    with respx.mock() as mock:
        mock.post("http://mlflow.test/api/2.0/mlflow/runs/create").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        client = await make_mlflow_client()
        try:
            run_id = await client.create_run(
                experiment_id="1",
                start_time_ms=1700000000000,
            )
        finally:
            await client._http.aclose()

    # MlflowClient.create_run returns the run_id string directly.
    assert isinstance(run_id, str)
    assert run_id  # non-empty


@pytest.mark.asyncio
async def test_get_run_parses_recorded_response(make_mlflow_client):
    """get_run should unwrap the 'run' envelope from the recorded shape."""
    with open(TAPE_DIR / "get_run.json") as f:
        recorded = json.load(f)

    with respx.mock() as mock:
        mock.get("http://mlflow.test/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        client = await make_mlflow_client()
        try:
            run = await client.get_run(run_id="r0")
        finally:
            await client._http.aclose()

    # MlflowClient.get_run returns resp["run"] — the inner run object.
    assert "info" in run
    assert "run_id" in run["info"]


@pytest.mark.asyncio
async def test_update_run_succeeds_against_recorded(make_mlflow_client):
    """update_run should accept the recorded /runs/update response without raising."""
    with open(TAPE_DIR / "terminate_run.json") as f:
        recorded = json.load(f)

    with respx.mock() as mock:
        mock.post("http://mlflow.test/api/2.0/mlflow/runs/update").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        client = await make_mlflow_client()
        try:
            # update_run returns None; assert it doesn't raise.
            result = await client.update_run(
                run_id="r0",
                status="FINISHED",
                end_time_ms=1700000000000,
            )
        finally:
            await client._http.aclose()

    assert result is None  # MlflowClient.update_run has no return value
