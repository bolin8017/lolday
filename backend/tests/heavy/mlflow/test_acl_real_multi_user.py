"""§10 #30 carryover — D2.3 Task 9 (real-MLflow ACL multi-user).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/routers/test_mlflow_authz.py covers
the ACL matrix with respx-mocked MLflow. This module locks the same
contract against a real MLflow 3.x server (testcontainers fixture
mlflow_url from backend/tests/heavy/conftest.py).

The invariant under test: experiments_proxy._mlflow_user_filter built
from user U1's UUID restricts search_runs to runs whose
tags["lolday.user_id"] == U1; runs created with U2's tag must not
appear in U1's filtered view. This is the production guarantee that
prevents user A from seeing user B's MLflow runs.

Marked heavy → runs in backend-slow.yml on main push + nightly.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio, pytest.mark.no_mock_mlflow]


def _build_user_filter(user_id: uuid.UUID) -> str:
    """Mirror of app.routers.experiments_proxy._mlflow_user_filter — kept
    inline so this heavy test does not need the FastAPI app to bootstrap."""
    return f"tags.\"lolday.user_id\" = '{user_id!s}'"


async def _create_tagged_run(
    http: httpx.AsyncClient,
    *,
    experiment_id: str,
    user_id: uuid.UUID,
) -> str:
    """Create an MLflow run carrying the tags.lolday.user_id=<U> tag."""
    start_ms = int(time.time() * 1000)
    resp = await http.post(
        "/api/2.0/mlflow/runs/create",
        json={
            "experiment_id": experiment_id,
            "start_time": start_ms,
            "tags": [{"key": "lolday.user_id", "value": str(user_id)}],
        },
    )
    resp.raise_for_status()
    return resp.json()["run"]["info"]["run_id"]


async def _search_runs(
    http: httpx.AsyncClient,
    *,
    experiment_id: str,
    filter_string: str,
) -> list[str]:
    resp = await http.post(
        "/api/2.0/mlflow/runs/search",
        json={
            "experiment_ids": [experiment_id],
            "filter": filter_string,
            "max_results": 100,
        },
    )
    resp.raise_for_status()
    return [r["info"]["run_id"] for r in resp.json().get("runs", [])]


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_mlflow_user_filter_restricts_to_owner(mlflow_url: str) -> None:
    """U1 creates run R1; U2 creates run R2; the U1-filter must see only R1."""
    u1 = uuid.uuid4()
    u2 = uuid.uuid4()
    async with httpx.AsyncClient(base_url=mlflow_url, timeout=30.0) as http:
        exp_resp = await http.post(
            "/api/2.0/mlflow/experiments/create",
            json={"name": f"acl-multi-user-{int(time.time())}"},
        )
        exp_resp.raise_for_status()
        experiment_id = exp_resp.json()["experiment_id"]

        r1 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u1)
        r2 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u2)

        u1_runs = await _search_runs(
            http, experiment_id=experiment_id, filter_string=_build_user_filter(u1)
        )
        assert r1 in u1_runs
        assert r2 not in u1_runs, (
            f"U1 filter leaked U2 run {r2!r}; this is the cross-user ACL bug "
            f"the _mlflow_user_filter guard exists to prevent. Got: {u1_runs}"
        )

        u2_runs = await _search_runs(
            http, experiment_id=experiment_id, filter_string=_build_user_filter(u2)
        )
        assert r2 in u2_runs
        assert r1 not in u2_runs


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_mlflow_admin_unscoped_search_sees_all(mlflow_url: str) -> None:
    """Without the user filter, both runs are visible (admin path)."""
    u1 = uuid.uuid4()
    u2 = uuid.uuid4()
    async with httpx.AsyncClient(base_url=mlflow_url, timeout=30.0) as http:
        exp_resp = await http.post(
            "/api/2.0/mlflow/experiments/create",
            json={"name": f"acl-admin-{int(time.time())}"},
        )
        exp_resp.raise_for_status()
        experiment_id = exp_resp.json()["experiment_id"]

        r1 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u1)
        r2 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u2)

        all_runs = await _search_runs(
            http, experiment_id=experiment_id, filter_string=""
        )
        assert {r1, r2}.issubset(set(all_runs))
