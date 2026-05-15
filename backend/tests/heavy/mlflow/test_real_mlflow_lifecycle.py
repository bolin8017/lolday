"""End-to-end exercise of MlflowClient against a real MLflow 3.x server.

Spins up the MLflow container via the heavy/conftest.py mlflow_url
session-scoped fixture. Verifies create → log → update → get cycle
parses correctly through every method on the MlflowClient class.

If a future MLflow version bump changes the REST API shape, this test
surfaces the breakage immediately.
"""

from __future__ import annotations

import time

import httpx
import pytest
from app.services.mlflow_client import MlflowClient

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


@pytest.mark.asyncio
@pytest.mark.timeout(
    120
)  # container boot + image pull can exceed the global 30s default
async def test_full_run_lifecycle(mlflow_url: str) -> None:
    """create_experiment → create_run → set_run_tag → update_run (FINISHED) → get_run.

    Exercises every method on MlflowClient that touches the run lifecycle.
    MlflowClient does not wrap /runs/log-metric or /runs/log-parameter — those
    endpoints are consumed directly by detector jobs via the mlflow-skinny SDK,
    not by the lolday backend. This test covers the platform's actual call
    surface: experiment + run management, tagging, status transition, and read-back.
    """
    base = mlflow_url
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as http:
        client = MlflowClient(tracking_uri=base, http_client=http)

        # --- create experiment ---
        exp_name = f"test-exp-{int(time.time())}"
        exp_id = await client.create_experiment(name=exp_name)
        assert isinstance(exp_id, str)
        assert exp_id  # non-empty string

        # --- get experiment by name round-trips the same ID ---
        fetched_exp = await client.get_experiment_by_name(name=exp_name)
        assert fetched_exp["experiment_id"] == exp_id
        assert fetched_exp["name"] == exp_name

        # --- set experiment-level tag ---
        await client.set_experiment_tag(
            experiment_id=exp_id, key="lolday.test", value="t21"
        )

        # --- create run (start_time_ms is required per MlflowClient docstring) ---
        start_ms = int(time.time() * 1000)
        run_id = await client.create_run(
            experiment_id=exp_id,
            start_time_ms=start_ms,
            tags=[{"key": "lolday.phase", "value": "t21"}],
        )
        assert isinstance(run_id, str)
        assert run_id  # non-empty string

        # --- set run-level tag ---
        await client.set_run_tag(run_id=run_id, key="lolday.test", value="t21")

        # --- update run to FINISHED ---
        end_ms = int(time.time() * 1000)
        await client.update_run(
            run_id=run_id,
            status="FINISHED",
            end_time_ms=end_ms,
        )

        # --- get_run returns the unwrapped run dict ---
        run = await client.get_run(run_id=run_id)
        # MlflowClient.get_run returns resp["run"] — so run["info"] is directly accessible
        assert run["info"]["run_id"] == run_id
        assert run["info"]["experiment_id"] == exp_id
        assert run["info"]["status"] == "FINISHED"

        # tags set via create_run and set_run_tag should appear under run["data"]["tags"]
        tags_by_key = {t["key"]: t["value"] for t in run["data"]["tags"]}
        assert tags_by_key.get("lolday.phase") == "t21"
        assert tags_by_key.get("lolday.test") == "t21"

        # --- search_runs finds the finished run ---
        runs = await client.search_runs(
            experiment_ids=[exp_id],
            filter_string="attributes.status = 'FINISHED'",
        )
        run_ids = [r["info"]["run_id"] for r in runs]
        assert run_id in run_ids
