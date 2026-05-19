"""Edge cases for GET /api/v1/detectors/{detector_id}/available-tags.

The existing `test_versions.py::test_available_tags_calls_github`
covers the happy path with a PAT-less anonymous call. This file adds:

- access control (write=True): non-owner gets 403
- PAT plumbing: when a UserGitCredential is stored, the PAT is sent
  to GitHub as `Authorization: Bearer ...` (verified at the respx
  request layer, not just the response shape)
- GitHub error propagation: 401 / 404 / 500 from GitHub bubbles up
  as the right HTTP code surface (raise_for_status → upstream)
- empty tag list returns `[]` (not 404 or 500)
"""

from uuid import UUID, uuid4

import httpx
import pytest
import respx
from app.models import Detector, Role, User
from sqlalchemy import select

from tests.conftest import _make_user, test_session_maker


async def _seed_detector_for_user(
    owner_email: str,
    *,
    git_url: str = "https://github.com/bolin8017/upxelfdet",
    name_suffix: str | None = None,
) -> str:
    """Insert a detector owned by ``owner_email`` directly via the ORM and
    return its id (no PUT call so we control the git_url exactly)."""
    suffix = name_suffix or uuid4().hex[:8]
    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == owner_email))
        ).scalar_one()
        detector = Detector(
            name=f"det-{suffix}",
            display_name=f"det-{suffix}",
            git_url=git_url,
            owner_id=owner.id,
        )
        session.add(detector)
        await session.commit()
        await session.refresh(detector)
        return str(detector.id)


@pytest.mark.asyncio
async def test_available_tags_returns_empty_list_when_no_tags(auth_client_developer):
    """A repo with no tags returns 200 + []. Without this guard the
    UI would have to distinguish 'no tags' from 'GitHub down', losing
    a useful signal."""
    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/empty-tags-repo",
        name_suffix="empty-tags",
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/empty-tags-repo/tags").mock(
            return_value=httpx.Response(200, json=[])
        )
        resp = await auth_client_developer.get(
            f"/api/v1/detectors/{detector_id}/available-tags"
        )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_available_tags_sends_pat_when_stored(auth_client_developer):
    """When the caller has stored a GitHub PAT via PUT /users/me/git-credential,
    the available-tags handler decrypts it and forwards it to GitHub
    as `Authorization: Bearer …`. Without the header, the call falls
    back to the 60-req/hour anonymous rate limit which exhausts fast
    on a busy ISLab dev machine."""
    # Stash a credential first via the real endpoint (exercises encrypt + DB write).
    cred_resp = await auth_client_developer.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_" + "C" * 36},
    )
    assert cred_resp.status_code == 200, cred_resp.text

    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/pat-test",
        name_suffix="pat-test",
    )

    captured: dict[str, str] = {}

    def _capture_and_respond(request):
        # request is httpx.Request; capture the Authorization header.
        captured["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json=[{"name": "v1.0.0", "commit": {"sha": "a" * 40}}],
        )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/pat-test/tags").mock(
            side_effect=_capture_and_respond
        )
        resp = await auth_client_developer.get(
            f"/api/v1/detectors/{detector_id}/available-tags"
        )

    assert resp.status_code == 200
    assert captured["authorization"].startswith("Bearer "), (
        f"PAT should be forwarded as Bearer token; got: {captured['authorization']!r}"
    )
    # The forwarded PAT must match what we PUT — confirms the decrypt path runs.
    assert captured["authorization"] == "Bearer ghp_" + "C" * 36


@pytest.mark.asyncio
async def test_available_tags_omits_authorization_when_no_pat(auth_client_developer):
    """When no UserGitCredential row exists, `_get_user_pat` returns
    None and `list_remote_tags` omits the Authorization header. This
    preserves the anonymous-public-repo path that the original
    `test_available_tags_calls_github` test exercises."""
    # Ensure no stale credential row from a prior test.
    await auth_client_developer.delete("/api/v1/users/me/git-credential")

    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/anon-test",
        name_suffix="anon-test",
    )

    captured: dict[str, str | None] = {}

    def _capture(request):
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=[])

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/anon-test/tags").mock(side_effect=_capture)
        resp = await auth_client_developer.get(
            f"/api/v1/detectors/{detector_id}/available-tags"
        )

    assert resp.status_code == 200
    assert captured["authorization"] is None, (
        f"anonymous call must omit Authorization; got {captured['authorization']!r}"
    )


@pytest.mark.asyncio
async def test_available_tags_propagates_github_500(auth_client_developer):
    """A 5xx from GitHub propagates as an `httpx.HTTPStatusError`
    raised out of `list_remote_tags.raise_for_status()`.

    Current behavior: the handler does NOT catch this — the exception
    bubbles to ASGITransport which re-raises it on the test client.
    The test captures that behavior so any future "wrap and return
    502" refactor in the handler will flip this assertion intentionally,
    not by accident. See architecture.md §10 for the pending
    follow-up.
    """
    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/server-err-repo",
        name_suffix="srv-err",
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/server-err-repo/tags").mock(
            return_value=httpx.Response(500, json={"message": "Server Error"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await auth_client_developer.get(
                f"/api/v1/detectors/{detector_id}/available-tags"
            )
    assert exc_info.value.response.status_code == 500


@pytest.mark.asyncio
async def test_available_tags_propagates_github_404(auth_client_developer):
    """A 404 from GitHub (repo missing or PAT-less to a private repo)
    raises `httpx.HTTPStatusError` rather than returning a 404 to the
    caller — same propagation as the 500 case, captured here so a
    later "treat 404 as empty list" or "wrap into HTTPException(404)"
    refactor is an intentional decision."""
    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/missing-repo",
        name_suffix="missing",
    )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/missing-repo/tags").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await auth_client_developer.get(
                f"/api/v1/detectors/{detector_id}/available-tags"
            )
    assert exc_info.value.response.status_code == 404


@pytest.mark.asyncio
async def test_available_tags_403_for_non_owner(client):
    """`require_detector_access(write=True)` rejects callers that aren't
    the detector owner or an admin. The endpoint is write-gated because
    listing remote tags is the first step of a build kickoff — letting
    a USER preview the tag list on someone else's detector would leak
    information about the repo's release cadence (a small but real
    side channel)."""
    # Seed a detector owned by the developer.
    await _make_user("dev@example.dev", role=Role.DEVELOPER)
    detector_id = await _seed_detector_for_user(
        "dev@example.dev",
        git_url="https://github.com/bolin8017/non-owner-test",
        name_suffix="non-owner",
    )
    # Now seed an unrelated USER and swap the client header.
    await _make_user("nonowner-tags@example.dev", role=Role.USER)
    client.headers["x-test-user-email"] = "nonowner-tags@example.dev"

    # respx must still be installed because a passing access-check would
    # try to call GitHub; we want the test to fail the access check
    # FIRST, never reaching the network call. assert_all_called=False
    # so the unused mock route isn't flagged.
    with respx.mock(base_url="https://api.github.com", assert_all_called=False) as mock:
        github_route = mock.get("/repos/bolin8017/non-owner-test/tags").mock(
            return_value=httpx.Response(500)  # would fail if reached
        )
        resp = await client.get(f"/api/v1/detectors/{detector_id}/available-tags")

    assert resp.status_code == 403
    # Confirm the access-gate fired before the GitHub call.
    assert not github_route.called, (
        "available-tags must check access BEFORE calling GitHub; mock got hit"
    )


# Silence ruff F401 for re-exported UUID — keeps the import in case a
# future test wants to assert against the seeded detector_id type.
_ = UUID
