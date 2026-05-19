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


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_falls_back_to_digest_when_last_tag():
    """When the target tag is the only tag, fall through to digest-level delete."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={"digest": "sha256:abc", "tags": [{"name": "v4.1.0"}]},
            )
        )
        digest_delete = mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(200))

        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_tag_or_artifact("detectors", "foo", "v4.1.0", "sha256:abc")

        assert digest_delete.called


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_idempotent_when_artifact_already_404():
    """Artifact 404 on the initial GET → silent return, no DELETE issued."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(return_value=httpx.Response(404))

        client = HarborClient("http://harbor", "admin", "pw")
        # Must not raise
        await client.delete_tag_or_artifact("detectors", "foo", "v4.1.0", "sha256:abc")

        # No DELETE hit Harbor
        assert all(call.request.method != "DELETE" for call in mock.calls)


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_silent_when_tag_not_in_artifact():
    """Tag is not on the artifact's tag list → silent return, no DELETE issued."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={"digest": "sha256:abc", "tags": [{"name": "v4.0.0"}]},
            )
        )

        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_tag_or_artifact("detectors", "foo", "v4.1.0", "sha256:abc")

        assert all(call.request.method != "DELETE" for call in mock.calls)


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_raises_on_5xx():
    """Harbor 5xx must surface as httpx.HTTPStatusError so the caller can log + count."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(return_value=httpx.Response(503))

        client = HarborClient("http://harbor", "admin", "pw")
        with pytest.raises(httpx.HTTPStatusError):
            await client.delete_tag_or_artifact(
                "detectors", "foo", "v4.1.0", "sha256:abc"
            )


@pytest.mark.asyncio
async def test_ensure_robot_account_uses_90d_duration_in_days_unit():
    """L-harbor-robot-rotate: new robots get a 90-day duration so the
    reconciler in T14 can renew them. Harbor's ``duration`` field is in
    DAYS per swagger (api/v2.0/swagger.yaml line 7800), not seconds; -1
    (no expiry) is forbidden."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots").mock(return_value=httpx.Response(200, json=[]))
        create_route = mock.post("/api/v2.0/robots").mock(
            return_value=httpx.Response(
                201, json={"name": "robot$build-pusher", "secret": "shh"}
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_robot_account("build-pusher", projects=["detectors"])

    # Inspect the JSON body sent in POST /robots.
    sent = create_route.calls.last.request
    import json as _json

    body = _json.loads(sent.content.decode())
    assert body["duration"] == 90  # 90 days (Harbor duration unit is days)


# ---------------------------------------------------------------------------
# get_artifact_digest — resolves a tag to its content-addressable digest.
# 404 must return None (caller treats as "not yet pushed"); 5xx must raise.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_artifact_digest_returns_digest_on_200():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors/repositories/foo/artifacts/v1.0.0").mock(
            return_value=httpx.Response(
                200, json={"digest": "sha256:abc123", "tags": [{"name": "v1.0.0"}]}
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        assert (
            await client.get_artifact_digest("detectors", "foo", "v1.0.0")
            == "sha256:abc123"
        )


@pytest.mark.asyncio
async def test_get_artifact_digest_returns_none_on_404():
    """Build reconciler poll path: 404 means 'not pushed yet', NOT a hard error.
    Silent return is what stops the poll loop spamming the reconciler error log."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors/repositories/foo/artifacts/v1.0.0").mock(
            return_value=httpx.Response(404)
        )
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.get_artifact_digest("detectors", "foo", "v1.0.0") is None


@pytest.mark.asyncio
async def test_get_artifact_digest_raises_on_5xx():
    """A genuine Harbor outage must propagate so the reconciler logs + counts it
    via BACKEND_ERRORS; swallowing 5xx would mask cluster-wide unhealthy state."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects/detectors/repositories/foo/artifacts/v1.0.0").mock(
            return_value=httpx.Response(503)
        )
        client = HarborClient("http://harbor", "admin", "pw")
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_artifact_digest("detectors", "foo", "v1.0.0")


# ---------------------------------------------------------------------------
# get_scan empty-overview branch — Harbor returns 200 with an empty
# scan_overview when an artifact has never been scanned.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scan_empty_overview_returns_not_scanned():
    """A freshly-pushed artifact has no scan_overview yet; the helper must
    return ScanStatus.NOT_SCANNED with all-zero counts so the reconciler
    knows to call trigger_scan."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:x"
        ).mock(
            return_value=httpx.Response(
                200, json={"digest": "sha256:x", "scan_overview": {}}
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:x")
        assert result.status == ScanStatus.NOT_SCANNED
        assert (result.critical, result.high, result.medium, result.low) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# delete_tag_or_artifact — the non-404 4xx branch hits line 236
# (resp.raise_for_status when status NOT in {200, 404}).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_tag_or_artifact_raises_on_403_after_head_succeeds():
    """The HEAD GET succeeds; the DELETE returns 403 (auth-misconfigured).
    Must propagate via raise_for_status so the caller doesn't silently keep
    the orphaned tag/artifact in Harbor."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc",
            params={"with_tag": "true"},
        ).mock(
            return_value=httpx.Response(
                200, json={"digest": "sha256:abc", "tags": [{"name": "v1.0.0"}]}
            )
        )
        mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(403))
        client = HarborClient("http://harbor", "admin", "pw")
        with pytest.raises(httpx.HTTPStatusError):
            await client.delete_tag_or_artifact(
                "detectors", "foo", "v1.0.0", "sha256:abc"
            )


# ---------------------------------------------------------------------------
# get_robot — L-harbor-robot-rotate helper. Harbor returns multiple robots
# matching the prefix query; the helper must return the exact ``robot$<name>``
# match (or None) and never disclose the ``secret`` field (Harbor doesn't
# return it on read).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_robot_returns_exact_prefix_match():
    """Multiple robots share the prefix; helper picks the canonical
    ``robot$<name>`` entry, not e.g. ``robot$other-project+name``."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots", params={"q": "name=build-pusher"}).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "robot$other-project+build-pusher",
                        "id": 1,
                        "duration": 90,
                    },
                    {"name": "robot$build-pusher", "id": 42, "duration": 90},
                ],
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        robot = await client.get_robot("build-pusher")
        assert robot is not None
        assert robot["id"] == 42
        assert robot["name"] == "robot$build-pusher"


@pytest.mark.asyncio
async def test_get_robot_returns_none_when_no_exact_match():
    """Prefix-matched robots exist but none have the exact name; helper must
    return None so the caller can call ``ensure_robot_account`` to create
    the missing canonical robot."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots", params={"q": "name=build-pusher"}).mock(
            return_value=httpx.Response(
                200, json=[{"name": "robot$other+build-pusher", "id": 1}]
            )
        )
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.get_robot("build-pusher") is None


@pytest.mark.asyncio
async def test_get_robot_empty_list_returns_none():
    """Harbor returns 200 with [] when no robot matches the prefix at all."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots", params={"q": "name=build-pusher"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        client = HarborClient("http://harbor", "admin", "pw")
        assert await client.get_robot("build-pusher") is None


# ---------------------------------------------------------------------------
# rotate_robot_secret — Harbor's RefreshSec contract: PATCH /robots/{id} with
# {"secret": ""} returns a freshly-generated secret in the response body.
# Pin the request shape so a regression in the call (using PUT, or sending a
# non-empty secret) fails loud rather than silently producing two robots
# rotating to each other's secrets.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_robot_secret_uses_patch_with_empty_secret():
    with respx.mock(base_url="http://harbor") as mock:
        route = mock.patch("/api/v2.0/robots/42").mock(
            return_value=httpx.Response(200, json={"secret": "fresh-shh"})
        )
        client = HarborClient("http://harbor", "admin", "pw")
        secret = await client.rotate_robot_secret(42)
        assert secret == "fresh-shh"

        import json as _json

        body = _json.loads(route.calls.last.request.content.decode())
        assert body == {"secret": ""}


# ---------------------------------------------------------------------------
# update_robot_duration — Harbor requires the full robot body on PUT; the
# helper must (a) fetch the current state, (b) strip read-only fields
# ``editable`` and ``expires_at``, (c) substitute ``duration``, (d) PUT
# the result. A regression that forgets to strip ``editable`` (read-only)
# would cause Harbor to 400 the PUT.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_robot_duration_strips_readonly_fields():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots/42").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "robot$build-pusher",
                    "duration": 30,
                    "expires_at": 1700000000,
                    "editable": True,
                    "permissions": [],
                },
            )
        )
        put_route = mock.put("/api/v2.0/robots/42").mock(
            return_value=httpx.Response(200)
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.update_robot_duration(42, duration_days=90)

        import json as _json

        sent = _json.loads(put_route.calls.last.request.content.decode())
        assert sent["duration"] == 90
        assert sent["id"] == 42
        assert "editable" not in sent
        assert "expires_at" not in sent


@pytest.mark.asyncio
async def test_update_robot_duration_handles_minus_one_never_expire():
    """Harbor's ``-1`` sentinel means 'never expire'; the helper must pass it
    through verbatim (legacy robots still use this value before T14
    migration)."""
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/robots/42").mock(
            return_value=httpx.Response(
                200, json={"id": 42, "name": "robot$x", "duration": -1}
            )
        )
        put_route = mock.put("/api/v2.0/robots/42").mock(
            return_value=httpx.Response(200)
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.update_robot_duration(42, duration_days=-1)

        import json as _json

        sent = _json.loads(put_route.calls.last.request.content.decode())
        assert sent["duration"] == -1
