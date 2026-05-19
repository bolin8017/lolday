import httpx
import pytest
import respx
from app.services.git import (
    check_repo_accessible,
    list_remote_tags,
    normalize_git_url,
    parse_github_owner_repo,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/user/repo", "https://github.com/user/repo.git"),
        ("https://github.com/user/repo.git", "https://github.com/user/repo.git"),
        ("https://github.com/user/repo/", "https://github.com/user/repo.git"),
        ("git@github.com:user/repo.git", "https://github.com/user/repo.git"),
        ("git@github.com:user/repo", "https://github.com/user/repo.git"),
        ("http://github.com/user/repo", "https://github.com/user/repo.git"),
        ("HTTPS://GitHub.com/User/Repo", "https://github.com/User/Repo.git"),
    ],
)
def test_normalize_github_urls(raw, expected):
    assert normalize_git_url(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "not a url",
        "https://example.com/foo/bar",  # non-GitHub host (v1 GitHub only)
        "https://github.com/only-one-segment",
        "",
        "git@github.com:user/repo/extra",  # SSH multi-segment path
        "https://github.com/user/repo?foo=bar",  # HTTPS with query string
        "https://github.com/user/repo#readme",  # HTTPS with fragment
        "git@github.com:user/repo.git?x=1",  # SSH with query string
    ],
)
def test_normalize_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_git_url(bad)


def test_parse_owner_repo():
    assert parse_github_owner_repo("https://github.com/user/repo.git") == (
        "user",
        "repo",
    )


def test_parse_owner_repo_rejects_wrong_segment_count():
    """The single-segment URL never makes it through normalize_git_url, but the
    parser is a public helper — guard the v1 contract that a non-2-segment path
    raises ValueError rather than silently returning a malformed tuple."""
    with pytest.raises(ValueError):
        parse_github_owner_repo("https://github.com/only-one-segment")


# ---------------------------------------------------------------------------
# list_remote_tags — exercises the GitHub REST API client. The respx tape
# pins the request shape so a regression in the call (path, headers, paging
# defaults) fails loud rather than producing silently-empty results.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_remote_tags_unauthenticated_shape():
    """No PAT: Authorization header is absent; tag list is mapped to the
    flat ``{'name', 'commit_sha'}`` shape used downstream."""
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/owner/repo/tags").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "v1.0.0", "commit": {"sha": "aaa111"}},
                    {"name": "v1.1.0", "commit": {"sha": "bbb222"}},
                ],
            )
        )
        tags = await list_remote_tags("owner", "repo")
        assert tags == [
            {"name": "v1.0.0", "commit_sha": "aaa111"},
            {"name": "v1.1.0", "commit_sha": "bbb222"},
        ]
        assert "Authorization" not in route.calls[0].request.headers


@pytest.mark.asyncio
async def test_list_remote_tags_authenticated_sets_bearer_header():
    """PAT supplied: the Authorization header carries the bearer token so
    GitHub grants the 5000 req/hour quota instead of the 60 req/hour
    anonymous bucket."""
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/owner/repo/tags").mock(
            return_value=httpx.Response(200, json=[])
        )
        await list_remote_tags("owner", "repo", pat="ghp_secret")
        assert route.calls[0].request.headers["Authorization"] == "Bearer ghp_secret"


@pytest.mark.asyncio
async def test_list_remote_tags_raises_on_http_error():
    """A 404 from GitHub bubbles up as ``httpx.HTTPStatusError`` (via
    ``resp.raise_for_status()``); the caller is expected to translate this."""
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/owner/repo/tags").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            await list_remote_tags("owner", "repo")


# ---------------------------------------------------------------------------
# check_repo_accessible — the v1 contract collapses 401 / 404 into ``False``
# (callers cannot distinguish). The tests pin all three branches so a future
# richer return-type refactor lands deliberately, not by accident.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_repo_accessible_200_true():
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/owner/repo").mock(return_value=httpx.Response(200, json={}))
        assert await check_repo_accessible("owner", "repo") is True


@pytest.mark.asyncio
async def test_check_repo_accessible_404_false():
    """Repo missing — returns False (caller treats as 'cannot register')."""
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/owner/repo").mock(return_value=httpx.Response(404))
        assert await check_repo_accessible("owner", "repo") is False


@pytest.mark.asyncio
async def test_check_repo_accessible_401_false_with_pat():
    """Bad PAT — returns False; helper does NOT distinguish from 404 in v1."""
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.get("/repos/owner/repo").mock(return_value=httpx.Response(401))
        assert await check_repo_accessible("owner", "repo", pat="bad-pat") is False
        assert route.calls[0].request.headers["Authorization"] == "Bearer bad-pat"
