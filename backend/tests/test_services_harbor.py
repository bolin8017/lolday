import httpx
import pytest
import respx
from app.services.harbor import HarborClient, ScanStatus


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
            return_value=httpx.Response(
                200, json=[{"name": "detectors", "project_id": 1}]
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_project("detectors", public=True)
        assert mock.calls.call_count == 1


@pytest.mark.asyncio
async def test_get_scan_parses_critical_high():
    with respx.mock(base_url="http://harbor") as mock:
        artifact_body = {
            "digest": "sha256:abc",
            "scan_overview": {
                "application/vnd.security.vulnerability.report; version=1.1": {
                    "scan_status": "Success",
                    "severity": "Critical",
                    "summary": {"summary": {"Critical": 2, "High": 5, "Medium": 10}},
                }
            },
        }
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(200, json=artifact_body))
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
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "robot$other-project+pusher", "id": 1},
                    {"name": "robot$pusher", "id": 2},
                ],
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.ensure_robot_account("pusher", projects=["detectors"])
        # Should find exact "robot$pusher" and skip creation
        assert result == {"name": "robot$pusher"}


@pytest.mark.asyncio
async def test_set_retention_policy_updates_when_retention_id_present():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project_id": 1,
                    "metadata": {"retention_id": 42},
                },
            )
        )
        mock.put("/api/v2.0/retentions/42").mock(return_value=httpx.Response(200))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.set_retention_policy("detectors", keep_n_recent=3)


@pytest.mark.asyncio
async def test_set_retention_policy_creates_when_no_retention_id():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors").mock(
            return_value=httpx.Response(
                200,
                json={
                    "project_id": 1,
                    "metadata": {},
                },
            )
        )
        mock.post("/api/v2.0/retentions").mock(return_value=httpx.Response(201))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.set_retention_policy("detectors", keep_n_recent=3)


@pytest.mark.asyncio
async def test_trigger_scan_202_returns_none():
    """Contract: returns None on success; raises on failure."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.post(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/scan"
        ).mock(return_value=httpx.Response(202))
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.trigger_scan("detectors", "foo", "sha256:x") is None


@pytest.mark.asyncio
async def test_trigger_scan_409_treated_as_success():
    """Harbor returns 409 if another caller already queued a scan for the
    same digest. Idempotent no-op, not a raise, or concurrent reconciler
    replicas would churn indefinitely.
    """
    with respx.mock(base_url="http://harbor") as mock:
        mock.post(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/scan"
        ).mock(
            return_value=httpx.Response(409, json={"errors": [{"code": "CONFLICT"}]})
        )
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.trigger_scan("detectors", "foo", "sha256:x") is None


@pytest.mark.asyncio
async def test_trigger_scan_500_raises_so_reconciler_can_log():
    """A Harbor 500 must surface as httpx.HTTPStatusError. Silencing it
    reproduces the exact "build stuck at scanning forever" class of bug
    the reconciler hook was added to prevent.
    """
    with respx.mock(base_url="http://harbor") as mock:
        mock.post(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x/scan"
        ).mock(return_value=httpx.Response(500))
        client = HarborClient("http://harbor", "admin", "pw")
        with pytest.raises(httpx.HTTPStatusError):
            await client.trigger_scan("detectors", "foo", "sha256:x")


@pytest.mark.asyncio
async def test_get_scan_unknown_status_falls_back_to_error():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "digest": "sha256:x",
                    "scan_overview": {
                        "application/vnd.security.vulnerability.report; version=1.1": {
                            "scan_status": "SomethingNewInHarbor2027",
                            "summary": {},
                        }
                    },
                },
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:x")
        assert result.status == ScanStatus.ERROR


@pytest.mark.asyncio
async def test_get_scan_status_error_preserved_not_silenced_as_zero():
    """Error must surface as ScanStatus.ERROR, not be coerced to NOT_SCANNED
    or SUCCESS. Callers branch on status, not counts.
    """
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:errd"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "digest": "sha256:errd",
                    "scan_overview": {
                        "application/vnd.security.vulnerability.report; version=1.1": {
                            "scan_status": "Error",
                            "summary": {},
                            "start_time": "2026-04-22T01:37:43.000Z",
                            "end_time": "2026-04-22T01:37:48.000Z",
                            "duration": 5,
                        }
                    },
                },
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:errd")
        assert result.status == ScanStatus.ERROR
        assert result.critical == 0
        assert result.high == 0


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_unpins_when_multi_tag():
    """Multiple tags share the manifest. DELETE must unpin only the target tag."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "digest": "sha256:abc",
                    "tags": [{"name": "4.1.0"}, {"name": "v4.1.0"}],
                },
            )
        )
        tag_delete = mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc/tags/4.1.0"
        ).mock(return_value=httpx.Response(200))

        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_tag_or_artifact("detectors", "foo", "4.1.0", "sha256:abc")

        assert tag_delete.called
        # Digest-level URL must NOT have been hit
        digest_delete_path = (
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        )
        for call in mock.calls:
            if call.request.method == "DELETE":
                assert call.request.url.path != digest_delete_path
