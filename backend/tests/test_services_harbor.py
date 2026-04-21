import httpx
import pytest
import respx

from app.services.harbor import HarborClient, ScanResult, ScanStatus


@pytest.mark.asyncio
async def test_ensure_project_creates_when_missing():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects", params={"name": "detectors"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.post("/api/v2.0/projects").mock(return_value=httpx.Response(201))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_project("detectors", public=True)
        assert mock.calls.call_count == 2


@pytest.mark.asyncio
async def test_ensure_project_skips_when_exists():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects", params={"name": "detectors"}).mock(
            return_value=httpx.Response(200, json=[{"name": "detectors", "project_id": 1}])
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_project("detectors", public=True)
        assert mock.calls.call_count == 1


@pytest.mark.asyncio
async def test_get_scan_parses_critical_high():
    with respx.mock(base_url="http://harbor") as mock:
        scan_body = {
            "application/vnd.security.vulnerability.report; version=1.1": {
                "scan_status": "Success",
                "severity": "Critical",
                "summary": {"summary": {"Critical": 2, "High": 5, "Medium": 10}},
            }
        }
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc/additions/vulnerabilities"
        ).mock(return_value=httpx.Response(200, json=scan_body))
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:abc")
        assert result.status == ScanStatus.SUCCESS
        assert result.critical == 2
        assert result.high == 5


@pytest.mark.asyncio
async def test_delete_artifact():
    with respx.mock(base_url="http://harbor") as mock:
        mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(200))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_artifact("detectors", "foo", "sha256:abc")
        assert mock.calls.call_count == 1


@pytest.mark.asyncio
async def test_ensure_robot_account_matches_only_exact_prefix():
    with respx.mock(base_url="http://harbor") as mock:
        # harbor returns two robots; only the exact prefix match should be recognized
        mock.get("/api/v2.0/robots").mock(
            return_value=httpx.Response(200, json=[
                {"name": "robot$other-project+pusher", "id": 1},
                {"name": "robot$pusher", "id": 2},
            ])
        )
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.ensure_robot_account("pusher", projects=["detectors"])
        # Should find exact "robot$pusher" and skip creation
        assert result == {"name": "robot$pusher"}


@pytest.mark.asyncio
async def test_set_retention_policy_updates_when_retention_id_present():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors").mock(
            return_value=httpx.Response(200, json={
                "project_id": 1,
                "metadata": {"retention_id": 42},
            })
        )
        mock.put("/api/v2.0/retentions/42").mock(return_value=httpx.Response(200))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.set_retention_policy("detectors", keep_n_recent=3)


@pytest.mark.asyncio
async def test_set_retention_policy_creates_when_no_retention_id():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors").mock(
            return_value=httpx.Response(200, json={
                "project_id": 1,
                "metadata": {},
            })
        )
        mock.post("/api/v2.0/retentions").mock(return_value=httpx.Response(201))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.set_retention_policy("detectors", keep_n_recent=3)


@pytest.mark.asyncio
async def test_trigger_scan_accepts_202():
    with respx.mock(base_url="http://harbor") as mock:
        mock.post(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/scan"
        ).mock(return_value=httpx.Response(202))
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.trigger_scan("detectors", "foo", "sha256:x") is True


@pytest.mark.asyncio
async def test_trigger_scan_returns_false_on_non_accepted():
    with respx.mock(base_url="http://harbor") as mock:
        mock.post(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/scan"
        ).mock(return_value=httpx.Response(409, json={"errors": [{"code": "CONFLICT"}]}))
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.trigger_scan("detectors", "foo", "sha256:x") is False


@pytest.mark.asyncio
async def test_get_scan_unknown_status_falls_back_to_error():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/additions/vulnerabilities"
        ).mock(return_value=httpx.Response(200, json={
            "application/vnd.security.vulnerability.report; version=1.1": {
                "scan_status": "SomethingNewInHarbor2027",
                "summary": {},
            }
        }))
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:x")
        assert result.status == ScanStatus.ERROR
