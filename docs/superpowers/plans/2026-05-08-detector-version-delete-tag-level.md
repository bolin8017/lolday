# Tag-level Harbor delete — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace digest-level Harbor delete with tag-aware delete in `HarborClient`, eliminating the shared-digest GC footgun discovered 2026-05-08 (`4.1.0` and `v4.1.0` shared a digest; deleting one took the other with it).

**Architecture:** A single new `HarborClient.delete_tag_or_artifact(project, repo, tag, digest)` method reads the artifact's tag list via `with_tag=true`, then issues tag-level `DELETE artifacts/{digest}/tags/{tag}` when more than one tag points at the manifest, falling back to digest-level delete only when the target is the last tag. All three production callers (`delete_version`, `_delete_harbor_images`, `_finalize_clean_scan`) migrate to the new method; the old `delete_artifact` is removed so digest-level delete is no longer publicly callable. The third caller (`_finalize_clean_scan` for CVE-blocked builds) was discovered during implementation as Task 6.5 — spec §5.2 was amended accordingly. Spec: `docs/superpowers/specs/2026-05-08-detector-version-delete-tag-level-design.md`.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (backend), `httpx` + `respx` (Harbor HTTP + tests), pytest with `pytest-asyncio` autouse mode.

---

## Files

**Modified:**

- `backend/app/services/harbor.py` — add `delete_tag_or_artifact`; remove `delete_artifact`.
- `backend/app/routers/detectors.py` — migrate two call sites (`delete_version` line 403-407, `_delete_harbor_images` line 182).
- `backend/app/reconciler/build_finalize.py` — migrate `_finalize_clean_scan` (CVE-blocked-build cleanup) — discovered as Task 6.5.
- `backend/tests/test_services_harbor.py` — add 5 unit tests for the new method; remove `test_delete_artifact`.
- `backend/tests/test_routers_detectors.py` — add `FakeHarborWithTags` helper, add 2 new tests (regression + helper coverage), update 2 existing tests for the new fake API.
- `backend/tests/test_reconciler.py`, `backend/tests/test_reconciler_notify.py` — update mocks/stubs to the new method (Task 6.5).
- `docs/architecture.md` — append §10 entry 17.
- `docs/runbooks/troubleshooting.md` — append recovery procedure.

**Untouched (asserted in spec §3):** `models/`, `schemas/`, `migrations/`, frontend, helm chart values.

---

## Tasks

### Task 1: HarborClient — multi-tag branch (drives skeleton)

**Files:**

- Modify: `backend/app/services/harbor.py`
- Test: `backend/tests/test_services_harbor.py`

Drives the initial skeleton. The test mocks Harbor with two tags pointing at the same digest; the impl must issue exactly one tag-level DELETE.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_services_harbor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_unpins_when_multi_tag -v
```

Expected: `AttributeError: 'HarborClient' object has no attribute 'delete_tag_or_artifact'`.

- [ ] **Step 3: Add the skeleton implementation**

In `backend/app/services/harbor.py`, add this method to `HarborClient` (place it just above `async def delete_artifact`):

```python
async def delete_tag_or_artifact(
    self, project: str, repo: str, tag: str, digest: str
) -> None:
    """Delete `tag`. Preserve other tags sharing the same manifest.

    Falls back to digest-level delete only when `tag` is the last tag
    on the manifest. Idempotent on missing artifact / missing tag.
    """
    async with self._client() as c:
        head = await c.get(
            f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}",
            params={"with_tag": "true"},
        )
        head.raise_for_status()
        tags = [t["name"] for t in (head.json().get("tags") or [])]

        if len(tags) > 1:
            url = (
                f"/api/v2.0/projects/{project}/repositories/{repo}"
                f"/artifacts/{digest}/tags/{tag}"
            )
        else:
            url = (
                f"/api/v2.0/projects/{project}/repositories/{repo}"
                f"/artifacts/{digest}"
            )
        resp = await c.delete(url)
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
```

(Idempotency on the GET 404 and the `tag not in tags` branch will be added in Tasks 3 and 4 — TDD-driven by their respective tests.)

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_unpins_when_multi_tag -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/harbor.py backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
feat(harbor): start delete_tag_or_artifact (multi-tag branch)

First TDD cycle for the tag-level Harbor delete. Reads the artifact's
tag list via `with_tag=true`, dispatches to tag-level DELETE when
more than one tag points at the manifest. Idempotency, single-tag
fallback, and error propagation arrive in subsequent commits.

Spec: docs/superpowers/specs/2026-05-08-detector-version-delete-tag-level-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: HarborClient — single-tag fallback

**Files:**

- Modify: `backend/app/services/harbor.py` (no change — current impl already covers; test confirms branch)
- Test: `backend/tests/test_services_harbor.py`

The skeleton from Task 1 already routes to digest-level when `len(tags) == 1`. This task adds a test that proves it.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_services_harbor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify behaviour**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_falls_back_to_digest_when_last_tag -v
```

Expected: PASS (the skeleton already implements the branch).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
test(harbor): cover single-tag fallback in delete_tag_or_artifact

Confirms the digest-level fallback when the target tag is the only
tag on the manifest. Behaviour matches the old delete_artifact for
this case; this test pins it so future refactors do not regress.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: HarborClient — idempotent on artifact 404

**Files:**

- Modify: `backend/app/services/harbor.py`
- Test: `backend/tests/test_services_harbor.py`

The skeleton currently calls `head.raise_for_status()`, which would raise on 404. The 404 case is "artifact already gone" — should be a silent return.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_services_harbor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_idempotent_when_artifact_already_404 -v
```

Expected: FAIL with `httpx.HTTPStatusError` (Client error '404 Not Found' from `raise_for_status`).

- [ ] **Step 3: Add the 404 short-circuit**

Edit `backend/app/services/harbor.py` `delete_tag_or_artifact` — replace the `head.raise_for_status()` line with an explicit 404 check followed by raise:

```python
        head = await c.get(
            f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}",
            params={"with_tag": "true"},
        )
        if head.status_code == 404:
            return
        head.raise_for_status()
        tags = [t["name"] for t in (head.json().get("tags") or [])]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_idempotent_when_artifact_already_404 -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/harbor.py backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
fix(harbor): make delete_tag_or_artifact idempotent on missing artifact

Treat 404 on the initial GET as "already gone" and return silently;
only genuine non-2xx (5xx, 401, 403) propagate. Matches the
idempotency posture of the old delete_artifact for this branch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: HarborClient — silent on tag absent from manifest

**Files:**

- Modify: `backend/app/services/harbor.py`
- Test: `backend/tests/test_services_harbor.py`

Race / drift case: the artifact still exists, but the requested tag is no longer on it (concurrent retag, manual cleanup). The current impl would issue a tag-level DELETE that returns 404 — accepted, but wastes a round-trip and obscures the intent.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_services_harbor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_silent_when_tag_not_in_artifact -v
```

Expected: FAIL with `respx.MockError` (no mock matched the DELETE request — the impl dispatched to digest-level since `len(tags) == 1`, then DELETE went out unmocked).

- [ ] **Step 3: Add the early return**

Edit `backend/app/services/harbor.py` `delete_tag_or_artifact` — insert the `tag not in tags` check immediately after computing `tags`:

```python
        tags = [t["name"] for t in (head.json().get("tags") or [])]
        if tag not in tags:
            return
```

The full method should now read:

```python
async def delete_tag_or_artifact(
    self, project: str, repo: str, tag: str, digest: str
) -> None:
    """Delete `tag`. Preserve other tags sharing the same manifest.

    Falls back to digest-level delete only when `tag` is the last tag
    on the manifest. Idempotent on missing artifact / missing tag.
    """
    async with self._client() as c:
        head = await c.get(
            f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}",
            params={"with_tag": "true"},
        )
        if head.status_code == 404:
            return
        head.raise_for_status()
        tags = [t["name"] for t in (head.json().get("tags") or [])]
        if tag not in tags:
            return

        if len(tags) > 1:
            url = (
                f"/api/v2.0/projects/{project}/repositories/{repo}"
                f"/artifacts/{digest}/tags/{tag}"
            )
        else:
            url = (
                f"/api/v2.0/projects/{project}/repositories/{repo}"
                f"/artifacts/{digest}"
            )
        resp = await c.delete(url)
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_silent_when_tag_not_in_artifact -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/harbor.py backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
fix(harbor): silence delete_tag_or_artifact when tag absent from manifest

Tag-not-on-manifest is a race / drift case: another path already
removed it. Return silently instead of issuing a no-op DELETE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: HarborClient — propagate 5xx

**Files:**

- Test: `backend/tests/test_services_harbor.py` only (impl already propagates via `raise_for_status`)

Documents the contract that genuine Harbor errors propagate.

- [ ] **Step 1: Write the test**

Append to `backend/tests/test_services_harbor.py`:

```python
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
            await client.delete_tag_or_artifact("detectors", "foo", "v4.1.0", "sha256:abc")
```

- [ ] **Step 2: Run test**

```bash
cd backend && uv run pytest tests/test_services_harbor.py::test_delete_tag_or_artifact_raises_on_5xx -v
```

Expected: PASS (impl already raises via `raise_for_status`).

- [ ] **Step 3: Run full HarborClient suite to confirm no regression**

```bash
cd backend && uv run pytest tests/test_services_harbor.py -v
```

Expected: every test in the file passes (including the legacy `test_delete_artifact`, which we delete in Task 8).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
test(harbor): cover 5xx propagation in delete_tag_or_artifact

Pins the contract that genuine Harbor errors propagate as
httpx.HTTPStatusError. Caller (router / helper) catches and logs
+ increments BACKEND_ERRORS{stage=...}.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Router — `delete_version` migration + regression test

**Files:**

- Modify: `backend/app/routers/detectors.py` (lines 403-407 of original; line numbers may shift after Tasks 1-5 are not in this file)
- Modify: `backend/tests/test_routers_detectors.py`

This is the central TDD cycle: a new regression test for the 2026-05-08 footgun (RED), updates to two existing tests (RED — they reference the old fake API), and the router migration that turns all three GREEN.

- [ ] **Step 1: Add `FakeHarborWithTags` helper at the top of the Phase 13a A4 test block**

Edit `backend/tests/test_routers_detectors.py`. Locate the section header `# Phase 13a A4 — DELETE /detectors/{id}/versions/{tag}` (around line 61). Insert the helper class immediately below it, before the first `@pytest.mark.asyncio`:

```python
class FakeHarborWithTags:
    """Fake HarborClient that tracks tag-level state.

    `tags` maps digest → list of tag names currently on that digest.
    `calls` records (method_name, project, repo, *args) tuples for assertions.
    """

    def __init__(self, *args, tags: dict[str, list[str]] | None = None, **kwargs):
        self.tags: dict[str, list[str]] = dict(tags or {})
        self.calls: list[tuple] = []

    async def delete_tag_or_artifact(
        self, project: str, repo: str, tag: str, digest: str
    ) -> None:
        self.calls.append(("delete_tag_or_artifact", project, repo, tag, digest))
        current = self.tags.get(digest, [])
        if tag not in current:
            return
        if len(current) > 1:
            current.remove(tag)
            self.tags[digest] = current
        else:
            self.tags.pop(digest, None)
```

- [ ] **Step 2: Update `test_delete_version_soft_deletes` to use the new fake**

Replace the body of `test_delete_version_soft_deletes` (around line 67-105) with:

```python
@pytest.mark.asyncio
async def test_delete_version_soft_deletes(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """Happy path: soft-deletes the version and best-effort purges Harbor."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        image_digest="sha256:abc",
    )

    fake = FakeHarborWithTags(tags={"sha256:abc": ["v1.0.0"]})
    monkeypatch.setattr(
        "app.routers.detectors.HarborClient", lambda *a, **k: fake
    )
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    list_resp = await async_client.get(
        f"/api/v1/detectors/{detector.id}/versions",
        headers=auth_owner_headers,
    )
    assert all(v["git_tag"] != "v1.0.0" for v in list_resp.json()["items"])
    assert fake.calls == [
        ("delete_tag_or_artifact", "detectors", "rfdet", "v1.0.0", "sha256:abc")
    ]
    # Last tag → digest-level delete → tags map empty
    assert "sha256:abc" not in fake.tags
```

- [ ] **Step 3: Update `test_delete_version_returns_204_when_harbor_purge_fails`**

Find that test (around line 235-275). Update its inline `FakeHarbor` class so the failing method is `delete_tag_or_artifact`:

```python
@pytest.mark.asyncio
async def test_delete_version_returns_204_when_harbor_purge_fails(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """If Harbor.delete_tag_or_artifact raises, the soft-delete commit must already
    have happened; the endpoint still returns 204 and the row stays DELETED.
    """
    detector = await detector_factory(name="rfdet")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        image_digest="sha256:abc",
    )

    class FakeHarborRaising:
        def __init__(self, *a, **kw):
            pass

        async def delete_tag_or_artifact(self, project, repo, tag, digest):
            raise RuntimeError("harbor down (simulated)")

    monkeypatch.setattr(
        "app.routers.detectors.HarborClient", FakeHarborRaising
    )
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204
```

- [ ] **Step 4: Add the new regression test**

Append to `backend/tests/test_routers_detectors.py` (after the existing delete_version tests, before the `delete_detector` block):

```python
@pytest.mark.asyncio
async def test_delete_version_only_unpins_target_tag_when_digest_shared(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    db_session,
    monkeypatch,
):
    """Two versions share image_digest. DELETE one tag → other tag survives.

    Regression for 2026-05-08 footgun: digest-level delete used to GC the
    shared manifest, leaving the surviving DB row pointing at Harbor 404.
    """
    from app.models.detector import DetectorVersionStatus

    detector = await detector_factory(name="rfdet")
    v_old = await version_factory(
        detector_id=detector.id, git_tag="4.1.0", image_digest="sha256:abc"
    )
    v_new = await version_factory(
        detector_id=detector.id, git_tag="v4.1.0", image_digest="sha256:abc"
    )

    fake = FakeHarborWithTags(tags={"sha256:abc": ["4.1.0", "v4.1.0"]})
    monkeypatch.setattr(
        "app.routers.detectors.HarborClient", lambda *a, **k: fake
    )
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/4.1.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    # Surviving version still ACTIVE in DB
    await db_session.refresh(v_new)
    assert v_new.status == DetectorVersionStatus.ACTIVE

    # Exactly one tag-level delete; the other tag still attached
    assert fake.calls == [
        ("delete_tag_or_artifact", "detectors", "rfdet", "4.1.0", "sha256:abc")
    ]
    assert fake.tags["sha256:abc"] == ["v4.1.0"]
```

- [ ] **Step 5: Run all three router tests, expect them all to FAIL (RED)**

```bash
cd backend && uv run pytest \
  tests/test_routers_detectors.py::test_delete_version_soft_deletes \
  tests/test_routers_detectors.py::test_delete_version_returns_204_when_harbor_purge_fails \
  tests/test_routers_detectors.py::test_delete_version_only_unpins_target_tag_when_digest_shared \
  -v
```

Expected: all three fail. Exact failure mode varies by test (the existing two will likely 500 because the router calls `delete_artifact` on a fake that no longer has it; the regression test will fail on the assertion because the digest-level delete cleared `fake.tags["sha256:abc"]`).

- [ ] **Step 6: Migrate `delete_version` to call the new method**

In `backend/app/routers/detectors.py`, find the `delete_version` body (the original line 403-407 region, just inside the `if settings.HARBOR_ADMIN_PASSWORD:` block). Replace:

```python
            await harbor.delete_artifact(
                "detectors",
                detector.name,
                version.image_digest,
            )
```

with:

```python
            await harbor.delete_tag_or_artifact(
                "detectors",
                detector.name,
                tag,
                version.image_digest,
            )
```

(The local `tag` parameter is the path arg of `delete_version`; no other change to surrounding logic.)

- [ ] **Step 7: Run the three tests again, expect GREEN**

```bash
cd backend && uv run pytest \
  tests/test_routers_detectors.py::test_delete_version_soft_deletes \
  tests/test_routers_detectors.py::test_delete_version_returns_204_when_harbor_purge_fails \
  tests/test_routers_detectors.py::test_delete_version_only_unpins_target_tag_when_digest_shared \
  -v
```

Expected: all three pass.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/test_routers_detectors.py backend/app/routers/detectors.py
git commit -m "$(cat <<'EOF'
fix(detectors): migrate delete_version to tag-level Harbor delete

Resolves the 2026-05-08 footgun. delete_version now calls
HarborClient.delete_tag_or_artifact, which unpins only the target
tag when more than one tag shares the manifest digest — preventing
the cascade GC that previously took sibling tags out with it.

Adds the regression test test_delete_version_only_unpins_target_tag_
when_digest_shared (two versions sharing image_digest, DELETE one,
the other survives in DB and Harbor). Adds FakeHarborWithTags helper
that models tag-list state for end-state assertions.

Spec: docs/superpowers/specs/2026-05-08-detector-version-delete-tag-level-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Router — `_delete_harbor_images` migration + helper test

**Files:**

- Modify: `backend/app/routers/detectors.py` (line 182 region)
- Modify: `backend/tests/test_routers_detectors.py`

The `delete_detector` flow calls `_delete_harbor_images` which loops over versions. Existing detector-deletion tests monkeypatch this helper to a noop (`backend/tests/test_detectors.py:71`), so the helper has zero direct test coverage. This task adds one targeted test and migrates the helper.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routers_detectors.py`, in the `delete_detector` test block (search for `test_delete_detector_blocks_when_in_flight` to find the area):

```python
@pytest.mark.asyncio
async def test_delete_detector_helper_routes_through_tag_level_delete(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """`_delete_harbor_images` (called from delete_detector) must use
    tag-level delete to stay consistent with delete_version and to
    prevent shared-digest GC if a future detector grows multi-tag rows.
    """
    detector = await detector_factory(name="rfdet")
    v1 = await version_factory(
        detector_id=detector.id, git_tag="v1.0.0", image_digest="sha256:aaa"
    )
    v2 = await version_factory(
        detector_id=detector.id, git_tag="v2.0.0", image_digest="sha256:bbb"
    )

    fake = FakeHarborWithTags(
        tags={"sha256:aaa": ["v1.0.0"], "sha256:bbb": ["v2.0.0"]}
    )
    monkeypatch.setattr(
        "app.routers.detectors.HarborClient", lambda *a, **k: fake
    )
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    method_names = {c[0] for c in fake.calls}
    assert method_names == {"delete_tag_or_artifact"}
    # Both versions cleaned up
    assert len(fake.calls) == 2
    assert ("delete_tag_or_artifact", "detectors", "rfdet", "v1.0.0", "sha256:aaa") in fake.calls
    assert ("delete_tag_or_artifact", "detectors", "rfdet", "v2.0.0", "sha256:bbb") in fake.calls
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && uv run pytest tests/test_routers_detectors.py::test_delete_detector_helper_routes_through_tag_level_delete -v
```

Expected: FAIL — `_delete_harbor_images` still calls `delete_artifact`, which `FakeHarborWithTags` does not implement; the request returns 204 (the try/except swallows the AttributeError) but `fake.calls` is empty so the assertions fail.

- [ ] **Step 3: Migrate `_delete_harbor_images`**

In `backend/app/routers/detectors.py`, find `_delete_harbor_images` (original line 157-193, line 182 is the call site). Replace:

```python
            await harbor.delete_artifact("detectors", detector_name, v.image_digest)
```

with:

```python
            await harbor.delete_tag_or_artifact(
                "detectors", detector_name, v.git_tag, v.image_digest
            )
```

The `v.status = DetectorVersionStatus.DELETED` line stays inside the try block (only mark deleted after Harbor success); no other change.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd backend && uv run pytest tests/test_routers_detectors.py::test_delete_detector_helper_routes_through_tag_level_delete -v
```

Expected: PASS.

- [ ] **Step 5: Run the full router test file to confirm no regression**

```bash
cd backend && uv run pytest tests/test_routers_detectors.py -v
```

Expected: every test passes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/detectors.py backend/tests/test_routers_detectors.py
git commit -m "$(cat <<'EOF'
fix(detectors): migrate _delete_harbor_images to tag-level Harbor delete

Both Harbor-touching call sites in routers/detectors.py now route
through HarborClient.delete_tag_or_artifact. The helper was
previously safe by accident (per-detector versions deleted together,
404 absorbed) but semantically wrong; this aligns it with
delete_version and forecloses the next footgun.

Adds direct test coverage for the helper, which previously had none
(callers monkeypatched it to noop).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Remove low-level `delete_artifact`

**Files:**

- Modify: `backend/app/services/harbor.py`
- Modify: `backend/tests/test_services_harbor.py`

After Tasks 6 and 7 the only remaining caller of `delete_artifact` was the test for `delete_artifact` itself. Remove both.

- [ ] **Step 1: Confirm zero callers**

```bash
cd backend && grep -rn "\.delete_artifact\b\|delete_artifact(" \
  app/ tests/ --include="*.py"
```

Expected output: only the method definition (`async def delete_artifact` in `app/services/harbor.py`) and the legacy `test_delete_artifact` in `tests/test_services_harbor.py`. No production code paths reference it.

- [ ] **Step 2: Remove the method from `HarborClient`**

In `backend/app/services/harbor.py`, delete the entire method:

```python
async def delete_artifact(self, project: str, repo: str, digest: str) -> None:
    async with self._client() as c:
        resp = await c.delete(
            f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}"
        )
        if resp.status_code not in (200, 404):
            resp.raise_for_status()
```

- [ ] **Step 3: Remove the legacy test**

In `backend/tests/test_services_harbor.py`, delete:

```python
@pytest.mark.asyncio
async def test_delete_artifact():
    with respx.mock(base_url="http://harbor") as mock:
        mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(200))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_artifact("detectors", "foo", "sha256:abc")
        assert mock.calls.call_count == 1
```

- [ ] **Step 4: Run full backend test suite**

```bash
cd backend && uv run pytest -v
```

Expected: every test passes. Mypy / ruff are run separately by pre-commit.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/harbor.py backend/tests/test_services_harbor.py
git commit -m "$(cat <<'EOF'
refactor(harbor): remove low-level delete_artifact (no remaining callers)

Both router callers migrated to delete_tag_or_artifact in the prior
two commits. Removing delete_artifact from the public HarborClient
surface so future digest-level deletes require writing a new method
explicitly — no more accidental footgun.

Breaking change to HarborClient public API; caller surface is
in-tree (zero external consumers).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: docs/architecture.md §10 — gotcha entry #17

**Files:**

- Modify: `docs/architecture.md`

- [ ] **Step 1: Append the entry**

Open `docs/architecture.md`. Find §10 "Common gotchas" — the last existing entry is #16 (Phase 6 backend FIFO scheduler, around line 402). Append entry #17 immediately after #16:

```markdown
17. **Harbor `image_digest` ≡ manifest GC unit, not tag** — `DetectorVersion.image_digest` maps to Harbor's manifest digest; one manifest can carry multiple tags (BuildKit cache hits on identical content, retag conventions, admin retags). `DELETE /api/v2.0/.../artifacts/{digest}` is digest-level: Harbor GCs the manifest and untags every tag pointing at it. Lolday must always go through `HarborClient.delete_tag_or_artifact(...)`, which reads `with_tag=true` first and uses tag-level `DELETE .../tags/{tag}` whenever more than one tag exists on the artifact. Footgun source: 2026-05-08 (`4.1.0` and `v4.1.0` shared a digest after a retag-convention change; digest-level delete pulled both). Fixed in PR #116.
```

`<TBD>` will be replaced with the actual PR number in Task 11 once the PR exists.

- [ ] **Step 2: Verify markdown rendering with prettier (dry-run)**

```bash
pre-commit run prettier --files docs/architecture.md
```

Expected: passes (or prettier reformats; in either case no behavioural issue).

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): add gotcha #17 — Harbor digest GC vs tag delete

Records the lolday `image_digest` ≡ Harbor manifest invariant and
points future contributors at HarborClient.delete_tag_or_artifact
as the only safe deletion path. Sources the entry in the 2026-05-08
shared-digest footgun.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: docs/runbooks/troubleshooting.md — recovery procedure

**Files:**

- Modify: `docs/runbooks/troubleshooting.md`

- [ ] **Step 1: Read the existing structure to find the Harbor section**

```bash
grep -n "^## \|^### " docs/runbooks/troubleshooting.md
```

Identify the Harbor / images section heading and the appropriate insertion point.

- [ ] **Step 2: Append the recovery procedure**

Append the following section after the last existing Harbor / images entry (or, if the runbook does not yet have a Harbor section, append it at the end with an `## Harbor / images` heading first). The block below uses a 4-backtick outer fence so the nested 3-backtick bash blocks render correctly — copy only the inner content, not the outer fence:

````markdown
## Active detector version disappears from Harbor (vcjob: ImagePullBackOff: not found)

**Symptom** — `DetectorVersion.status = ACTIVE` in DB and `image_digest` populated; vcjob fails to pull `harbor.lolday.svc:80/detectors/<name>:<tag>` with 404; `docker pull` of the same tag also returns 404.

**Read** — Harbor no longer has the tag/digest. Likely sources:

1. Pre-v0.20.7 digest-level delete footgun — sibling tag deletion took the manifest with it.
2. Retention policy GC.
3. Manual Harbor cleanup.

**Recovery (preferred)** — re-build through the lolday API:

```bash
JWT=...   # CF Access token; copy from browser cookie / DevTools
DET_ID=...
curl -X POST "https://lolday.../api/v1/detectors/$DET_ID/builds" \
  -H "Cookie: CF_Authorization=$JWT" \
  -H "Content-Type: application/json" \
  -d '{"git_tag": "v4.1.0"}'
```

The unique constraint `(detector_id, git_tag)` blocks a duplicate ACTIVE row. If a stale ACTIVE row exists pointing at the missing image, soft-delete it first (`DELETE /api/v1/detectors/$DET_ID/versions/<tag>`) before re-building. The new build pushes the same content; BuildKit usually reproduces the original digest.

**Fallback (Harbor writable but the build pipeline is broken)** — pull from a workstation cache and re-push to Harbor:

```bash
docker pull harbor.lolday.svc:80/detectors/<name>:<tag>   # confirms the 404
# from a workstation that still has the image cached:
docker tag  <local-image> harbor.lolday.svc:80/detectors/<name>:<tag>
docker push harbor.lolday.svc:80/detectors/<name>:<tag>
```

Detector images are not in CI's GHCR registry today (CI builds backend / frontend / helpers; detector images are operator-built). The fallback applies only if a workstation kept the image in its local docker cache.

**Prevention** — v0.20.7+ uses tag-level Harbor delete; multi-tag-shared-digest scenarios no longer cascade.
````

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/troubleshooting.md
git commit -m "$(cat <<'EOF'
docs(runbooks): add recovery procedure for accidentally GC'd Harbor image

Runbook entry for the 2026-05-08 footgun symptom (vcjob
ImagePullBackOff: not found while DB row is ACTIVE) plus its
recovery via POST /api/v1/detectors/$ID/builds (preferred) or
docker push from a workstation cache (fallback).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Final verification + push branch + open PR

**Files:** none modified — orchestration only.

- [ ] **Step 1: Run the full backend test suite**

```bash
cd backend && uv run pytest -v
```

Expected: every test passes. If any test fails, stop and diagnose root cause; do not push.

- [ ] **Step 2: Run pre-commit on every changed file**

```bash
pre-commit run --all-files
```

Expected: passes. Any auto-formatting changes get amended into the relevant commit (or a small `style: ...` commit).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin fix/detector-version-tag-level-delete
```

- [ ] **Step 4: Open PR via gh CLI**

```bash
gh pr create \
  --base main \
  --head fix/detector-version-tag-level-delete \
  --title "fix(detectors): tag-level Harbor delete prevents shared-digest GC footgun" \
  --body "$(cat <<'EOF'
## Summary

- Replaces digest-level Harbor delete with a single tag-aware method `HarborClient.delete_tag_or_artifact`.
- Migrates both call sites (`delete_version`, `_delete_harbor_images`) and removes the legacy `delete_artifact` so digest-level deletes are no longer publicly callable.
- Adds the regression test for the 2026-05-08 footgun (two versions sharing `image_digest` → DELETE one, the other survives) plus the first direct test for the detector-delete helper.
- Adds `docs/architecture.md` §10 entry #17 and a recovery runbook in `docs/runbooks/troubleshooting.md`.

## Why

`DELETE /api/v1/detectors/{id}/versions/{tag}` was issuing `DELETE /api/v2.0/.../artifacts/{digest}` — Harbor's digest-level delete, which GCs the manifest and untags every tag pointing at it. On 2026-05-08 the `4.1.0 → v4.1.0` retag-convention change left two `DetectorVersion` rows sharing one digest; deleting the older row cascaded into the active one and produced `ImagePullBackOff: not found` on the next train job.

## Spec / Plan

- Spec: `docs/superpowers/specs/2026-05-08-detector-version-delete-tag-level-design.md`
- Plan: `docs/superpowers/plans/2026-05-08-detector-version-delete-tag-level.md`

## Test plan

- [x] `uv run pytest backend/tests/test_services_harbor.py` (5 new tests for `delete_tag_or_artifact`; legacy `test_delete_artifact` removed)
- [x] `uv run pytest backend/tests/test_routers_detectors.py` (new shared-digest regression + new helper test + 2 updated existing tests)
- [x] `uv run pytest` (full backend suite green)
- [x] `pre-commit run --all-files`
- [ ] Cluster smoke test post-deploy: build a detector at `v4.1.0`, retag in Harbor so `cleanup-test` shares the digest, `DELETE .../versions/cleanup-test`, verify `v4.1.0` survives in DB and Harbor
EOF
)"
```

- [ ] **Step 5: Backfill the PR number into the architecture doc**

Once the PR is created and you have the number, replace `PR #<TBD>` in the new entry of `docs/architecture.md` with the actual `PR #<N>`. Commit and push:

```bash
sed -i 's/Fixed in PR #<TBD>/Fixed in PR #<N>/' docs/architecture.md  # replace <N> with the real number
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): backfill PR number for gotcha #17

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push
```

- [ ] **Step 6: Wait for CI green, then squash-merge**

CI gates: `lint`, `backend`, `frontend`, `helm`, `images`, `helpers` (the last three should be no-ops because the paths they watch are unchanged, but the workflow still reports a status). Once all green, squash-merge into `main` via the GitHub UI or:

```bash
gh pr merge --squash --delete-branch
```

The release cut (`chore(release): cut v0.20.7`), image rebuild, and cluster rollout follow as separate operator-driven steps per spec §9.2-9.4. They are out of scope for this implementation plan because the chart-version bump and the `kubectl set image` are typically operator decisions on timing.
