import httpx
import pytest
import respx


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_experiments_list_proxied(user_client):
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.post(
            "http://mlflow.lolday.svc:5000/api/2.0/mlflow/experiments/search"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"experiments": [{"experiment_id": "1", "name": "detector:x:v1"}]},
            )
        )
        r = await user_client.get("/api/v1/experiments")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "detector:x:v1"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_runs_list_for_experiment(user_client):
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.post("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "runs": [
                        {"info": {"run_id": "r1", "status": "FINISHED"}, "data": {}}
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
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"run_id": "r1"},
                        "data": {"metrics": [], "params": []},
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
