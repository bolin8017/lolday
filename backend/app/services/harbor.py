import enum
from dataclasses import dataclass

import httpx


class ScanStatus(str, enum.Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    ERROR = "Error"
    NOT_SCANNED = "NotScanned"


@dataclass
class ScanResult:
    status: ScanStatus
    critical: int
    high: int
    medium: int
    low: int


class HarborClient:
    """Thin async client for Harbor REST v2.0."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)

    def _client(self) -> httpx.AsyncClient:
        # Harbor sets a `sid` session cookie on first response, then enforces
        # CSRF on subsequent requests carrying that cookie (goharbor/harbor#10890).
        # Clearing cookies before each request avoids this for API-only callers.
        async def _strip_session_cookies(request: httpx.Request) -> None:
            request.headers.pop("cookie", None)

        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self._auth,
            headers={"X-Harbor-CSRF-Token": ""},
            event_hooks={"request": [_strip_session_cookies]},
            timeout=15,
        )

    async def ensure_project(self, name: str, public: bool = True) -> None:
        async with self._client() as c:
            resp = await c.get("/api/v2.0/projects", params={"name": name})
            resp.raise_for_status()
            existing = [p for p in resp.json() if p.get("name") == name]
            if existing:
                return
            create = await c.post(
                "/api/v2.0/projects",
                json={
                    "project_name": name,
                    "metadata": {"public": "true" if public else "false"},
                },
            )
            create.raise_for_status()

    async def ensure_robot_account(self, name: str, projects: list[str]) -> dict:
        """Idempotent robot account creation. Returns {'name': ..., 'secret': ...} on creation,
        or {'name': ...} if already exists (secret cannot be retrieved later)."""
        async with self._client() as c:
            resp = await c.get("/api/v2.0/robots", params={"q": f"name={name}"})
            resp.raise_for_status()
            expected = f"robot${name}"
            matches = [r for r in resp.json() if r.get("name") == expected]
            if matches:
                return {"name": matches[0]["name"]}
            permissions = [
                {
                    "kind": "project",
                    "namespace": p,
                    "access": [
                        {"resource": "repository", "action": "pull"},
                        {"resource": "repository", "action": "push"},
                    ],
                }
                for p in projects
            ]
            create = await c.post(
                "/api/v2.0/robots",
                json={
                    "name": name,
                    "description": "lolday build pusher",
                    "disable": False,
                    "level": "system",
                    "duration": -1,
                    "permissions": permissions,
                },
            )
            create.raise_for_status()
            return create.json()

    async def set_retention_policy(self, project: str, keep_n_recent: int) -> None:
        """Create or replace retention policy: keep N most recent tags."""
        async with self._client() as c:
            resp = await c.get(f"/api/v2.0/projects/{project}")
            resp.raise_for_status()
            project_data = resp.json()
            project_id = project_data["project_id"]
            retention_id = (project_data.get("metadata") or {}).get("retention_id")
            rule = {
                "algorithm": "or",
                "rules": [
                    {
                        "disabled": False,
                        "action": "retain",
                        "scope_selectors": {
                            "repository": [
                                {
                                    "kind": "doublestar",
                                    "decoration": "repoMatches",
                                    "pattern": "**",
                                }
                            ]
                        },
                        "tag_selectors": [
                            {
                                "kind": "doublestar",
                                "decoration": "matches",
                                "pattern": "**",
                            }
                        ],
                        "params": {"latestPushedK": keep_n_recent},
                        "template": "latestPushedK",
                    }
                ],
                "trigger": {"kind": "Schedule", "settings": {"cron": "0 0 2 * * 0"}},
                "scope": {"level": "project", "ref": project_id},
            }
            if retention_id:
                put_resp = await c.put(
                    f"/api/v2.0/retentions/{retention_id}", json=rule
                )
                put_resp.raise_for_status()
            else:
                post_resp = await c.post("/api/v2.0/retentions", json=rule)
                post_resp.raise_for_status()

    async def get_artifact_digest(
        self, project: str, repo: str, tag: str
    ) -> str | None:
        async with self._client() as c:
            resp = await c.get(
                f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{tag}"
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("digest")

    async def get_scan(self, project: str, repo: str, digest: str) -> ScanResult:
        async with self._client() as c:
            resp = await c.get(
                f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}",
                params={"with_scan_overview": "true"},
            )
            resp.raise_for_status()
            scan_overview = resp.json().get("scan_overview") or {}
            if not scan_overview:
                return ScanResult(ScanStatus.NOT_SCANNED, 0, 0, 0, 0)
            report = next(iter(scan_overview.values()))
            raw = report.get("scan_status", "NotScanned")
            try:
                status = ScanStatus(raw)
            except ValueError:
                status = ScanStatus.ERROR
            summary = (report.get("summary") or {}).get("summary") or {}
            return ScanResult(
                status=status,
                critical=summary.get("Critical", 0),
                high=summary.get("High", 0),
                medium=summary.get("Medium", 0),
                low=summary.get("Low", 0),
            )

    async def get_image_labels(
        self, project: str, repository: str, digest: str
    ) -> dict[str, str]:
        """Return OCI image config Labels dict (may be empty).

        Harbor exposes these via ``/api/v2.0/projects/.../artifacts/<digest>`` at
        ``extra_attrs.config.Labels``.
        """
        url = (
            f"/api/v2.0/projects/{project}/repositories/{repository}/artifacts/{digest}"
        )
        async with self._client() as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.json()
        config = (data.get("extra_attrs") or {}).get("config") or {}
        labels = config.get("Labels") or {}
        return dict(labels)

    async def delete_artifact(self, project: str, repo: str, digest: str) -> None:
        async with self._client() as c:
            resp = await c.delete(
                f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}"
            )
            if resp.status_code not in (200, 404):
                resp.raise_for_status()

    async def trigger_scan(self, project: str, repo: str, digest: str) -> None:
        """Kick off a Trivy scan on an artifact. Raises on failure.

        Harbor does not auto-scan on push — without an explicit POST the
        build reconciler sees ``scan_status: NotScanned`` forever. Safe to
        call multiple times: Harbor's 409 response on "scan already queued"
        is treated as success since another caller already did the right
        thing. All other non-2xx responses + network errors propagate via
        ``httpx.HTTPError`` so the caller can log + count them rather than
        silently loop.
        """
        async with self._client() as c:
            resp = await c.post(
                f"/api/v2.0/projects/{project}/repositories/{repo}/"
                f"artifacts/{digest}/scan"
            )
            if resp.status_code in (200, 202, 409):
                return
            resp.raise_for_status()
