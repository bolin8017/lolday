import re
from urllib.parse import urlparse

import httpx

from app.config import settings

_GITHUB_SSH_RE = re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE)
_GITHUB_HTTPS_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE
)


def normalize_git_url(raw: str) -> str:
    """Normalize any supported GitHub URL form to canonical HTTPS form.

    Supports: https(s)://, http(s)://, git@github.com:user/repo.git, trailing .git / slash variants.
    Only GitHub is supported in v1.
    """
    if not raw or not raw.strip():
        raise ValueError("empty git url")
    raw = raw.strip()
    if "?" in raw or "#" in raw:
        raise ValueError(f"unsupported or invalid git url (query/fragment not allowed): {raw}")

    m = _GITHUB_SSH_RE.match(raw)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}.git"

    m = _GITHUB_HTTPS_RE.match(raw)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}.git"

    raise ValueError(f"unsupported or invalid git url: {raw}")


def parse_github_owner_repo(normalized_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a normalized GitHub URL."""
    parsed = urlparse(normalized_url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2:
        raise ValueError(f"cannot parse owner/repo from {normalized_url}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


async def list_remote_tags(owner: str, repo: str, pat: str | None = None) -> list[dict]:
    """List tags via GitHub REST API. Returns [{'name': str, 'commit_sha': str}, ...].

    Uses unauthenticated requests when pat is None (for public repos); subject to
    lower GitHub rate limit (60 req/hour per IP). With pat, 5000 req/hour.

    Returns at most 30 tags (GitHub default page size). Pagination is not
    implemented in v1; if a repo has >30 tags, add per_page=100 + page iteration.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    async with httpx.AsyncClient(base_url=settings.GITHUB_API_URL, timeout=10) as client:
        resp = await client.get(f"/repos/{owner}/{repo}/tags", headers=headers)
        resp.raise_for_status()
        return [
            {"name": t["name"], "commit_sha": t["commit"]["sha"]}
            for t in resp.json()
        ]


async def check_repo_accessible(owner: str, repo: str, pat: str | None = None) -> bool:
    """Return True if the repo exists and is accessible with optional PAT.

    Returns False for both 401 (bad PAT) and 404 (repo missing); callers cannot
    distinguish these cases from the return value alone. Acceptable for v1 where
    we only gate registration on "PAT can see this repo"; future auth-flow
    diagnostics will need a richer return type.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    async with httpx.AsyncClient(base_url=settings.GITHUB_API_URL, timeout=10) as client:
        resp = await client.get(f"/repos/{owner}/{repo}", headers=headers)
        return resp.status_code == 200
