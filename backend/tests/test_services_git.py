import pytest
from app.services.git import normalize_git_url, parse_github_owner_repo


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
