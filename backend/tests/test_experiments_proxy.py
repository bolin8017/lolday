import httpx
import pytest
import respx
from app.models import User
from sqlalchemy import select

from tests.conftest import test_session_maker as _test_session_maker


async def _user_id_for_email(email: str) -> str:
    """Look up the UUID (as str) for a seeded test user."""
    async with _test_session_maker() as session:
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
    return str(row.id)


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_experiments_list_proxied(user_client):
    """list_experiments: non-admin user with at least one owned run sees the experiment.

    The H-1 ACL adds a per-experiment filter that issues one ``search_runs``
    per experiment, scoped to ``tags."lolday.user_id" = '<caller-uuid>'``.
    We mock that follow-up call to return one run so the experiment is kept.
    """
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.post(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow/experiments/search"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"experiments": [{"experiment_id": "1", "name": "detector:x:v1"}]},
            )
        )
        mock.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "info": {"run_id": "any", "experiment_id": "1"},
                            "data": {"tags": [{"key": "lolday.user_id", "value": uid}]},
                        }
                    ]
                },
            )
        )
        r = await user_client.get("/api/v1/experiments")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "detector:x:v1"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_runs_list_for_experiment(user_client):
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "info": {"run_id": "r1", "status": "FINISHED"},
                            "data": {"tags": [{"key": "lolday.user_id", "value": uid}]},
                        }
                    ]
                },
            )
        )
        r = await user_client.get("/api/v1/experiments/1/runs")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_get_run_proxied(user_client):
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"run_id": "r1"},
                        "data": {
                            "metrics": [],
                            "params": [],
                            "tags": [{"key": "lolday.user_id", "value": uid}],
                        },
                    }
                },
            )
        )
        r = await user_client.get("/api/v1/runs/r1")
    assert r.status_code == 200
    # Proxy now flattens MLflow's nested {info, data} into a flat shape.
    assert r.json()["run_id"] == "r1"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_error_proxied_as_502(user_client):
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(500, json={"error_code": "INTERNAL"}),
        )
        r = await user_client.get("/api/v1/runs/r1")
    assert r.status_code == 502


# ---------------------------------------------------------------------------
# H-1: per-user ACL on the five proxy handlers.
# ---------------------------------------------------------------------------


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_list_runs_filters_by_owner(user_client, second_user_client):
    """user A submits a run; user B should not see it via the proxy."""
    uid_a = await _user_id_for_email("user1@example.dev")
    uid_b = await _user_id_for_email("user2@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "info": {"run_id": "r-a", "experiment_id": "1"},
                            "data": {
                                "tags": [{"key": "lolday.user_id", "value": uid_a}]
                            },
                        },
                        {
                            "info": {"run_id": "r-b", "experiment_id": "1"},
                            "data": {
                                "tags": [{"key": "lolday.user_id", "value": uid_b}]
                            },
                        },
                    ]
                },
            )
        )
        r = await user_client.get("/api/v1/experiments/1/runs")

    assert r.status_code == 200, r.text
    run_ids = {x["run_id"] for x in r.json()}
    assert "r-a" in run_ids
    assert "r-b" not in run_ids


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_get_run_404s_for_non_owner(user_client, second_user_client):
    """user A owns the run; user B requesting it via GET /runs/{id} gets 404 (not 403).

    The ``user_client`` fixture is injected purely to seed user1@example.dev
    so we can stamp its UUID into the run tag; we then issue the request as
    ``second_user_client`` (user2@example.dev) and expect a 404.
    """
    uid_a = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"run_id": "r-a"},
                        "data": {
                            "metrics": [],
                            "params": [],
                            "tags": [{"key": "lolday.user_id", "value": uid_a}],
                        },
                    }
                },
            )
        )
        r = await second_user_client.get("/api/v1/runs/r-a")
    assert r.status_code == 404, r.text


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_admin_sees_all_runs(auth_client_admin):
    """Admin bypasses the owner filter — sees runs tagged with another user's UUID."""
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"run_id": "r-a"},
                        "data": {
                            "metrics": [],
                            "params": [],
                            "tags": [
                                {
                                    "key": "lolday.user_id",
                                    "value": "00000000-0000-0000-0000-000000000001",
                                }
                            ],
                        },
                    }
                },
            )
        )
        r = await auth_client_admin.get("/api/v1/runs/r-a")
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == "r-a"


# ---------------------------------------------------------------------------
# H-2: artifact path traversal / absolute-path guard.
# ---------------------------------------------------------------------------


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_rejects_dotdot(user_client):
    """``..`` segments in the artifact path must 400 before any upstream call."""
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "r-a",
                            "artifact_uri": "mlflow-artifacts:/1/r-a/artifacts",
                        },
                        "data": {
                            "metrics": [],
                            "params": [],
                            "tags": [{"key": "lolday.user_id", "value": uid}],
                        },
                    }
                },
            )
        )
        r = await user_client.get(
            "/api/v1/runs/r-a/artifacts/download?path=../../other-run/model.bin"
        )
    assert r.status_code == 400, r.text


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_download_artifact_rejects_absolute_path(user_client):
    """Absolute paths in the artifact path must 400."""
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {
                            "run_id": "r-a",
                            "artifact_uri": "mlflow-artifacts:/1/r-a/artifacts",
                        },
                        "data": {
                            "metrics": [],
                            "params": [],
                            "tags": [{"key": "lolday.user_id", "value": uid}],
                        },
                    }
                },
            )
        )
        r = await user_client.get(
            "/api/v1/runs/r-a/artifacts/download?path=/etc/passwd"
        )
    assert r.status_code == 400, r.text
