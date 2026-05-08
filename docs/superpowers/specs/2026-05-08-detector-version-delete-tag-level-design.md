# Detector version delete — tag-level Harbor delete prevents shared-digest GC

> 2026-05-08 · scope: `backend/app/services/harbor.py`, `backend/app/routers/detectors.py`, `backend/tests/test_services_harbor.py`, `backend/tests/test_routers_detectors.py`, `docs/architecture.md`, `docs/runbooks/troubleshooting.md`.
>
> Continues the v0.20.x submit-job / detector-lifecycle hardening line: #112 (detector-version override toggle removal) → #113/#114 (threshold field eradication, platform stance) → #115 (UI polish, v0.20.6 cut). This spec fixes a footgun discovered during 2026-05-08 cleanup after v0.20.6 release: two `DetectorVersion` rows shared `image_digest` (content-identical rebuild after the `4.1.0` → `v4.1.0` retag convention change); calling `DELETE .../versions/4.1.0` triggered a digest-level Harbor delete that GC'd the shared manifest, leaving `v4.1.0` (still ACTIVE in DB) pointing at a Harbor 404. Subsequent train jobs failed with `ImagePullBackOff: not found`.

## 1. Why

`backend/app/routers/detectors.py:403-407` (`delete_version`) and `backend/app/routers/detectors.py:182` (`_delete_harbor_images` helper) call:

```python
await harbor.delete_artifact(project, repo, version.image_digest)
```

`HarborClient.delete_artifact` (`backend/app/services/harbor.py:199-205`) issues:

```
DELETE /api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}
```

This is **digest-level**: Harbor GCs the entire manifest and removes every tag pointing at it. Two `DetectorVersion` rows can legitimately share a digest — BuildKit cache hits on identical content, renaming a git-tag convention, admin retags. The delete designed to remove one tag instead nukes the manifest underlying every sibling tag.

**Root cause** is a semantic mismatch: lolday receives tag-level user intent (`DELETE .../versions/{tag}`) and executes a digest-level Harbor action. In OCI, a tag is a mutable label and a digest is an immutable content-addressable manifest reference; the two are not interchangeable.

The mismatch lives at two layers:

1. **Caller layer** — both `delete_version` and `_delete_harbor_images` reach for `delete_artifact(digest)` instead of an operation that takes the user's tag.
2. **Service layer** — `HarborClient.delete_artifact(project, repo, digest)` is the only deletion method on the public surface, and it is digest-level. Any future caller wanting to remove "a version" will land on the same footgun.

## 2. Decisions

| #   | Topic                     | Decision                                                                                                                                                                                                                                                                                                                              |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Q1  | Delete semantic           | A — tag-aware delete. `DELETE` keeps cleaning Harbor; only the targeted tag is unpinned. Manifest GC happens only when the tag is the last tag pointing at it. Aligns lolday with the OCI registry industry default (GHCR / GCR / Quay / ECR / Harbor's own GUI).                                                                     |
| Q2  | Service-layer API surface | (i) — replace `HarborClient.delete_artifact` with a single high-level `delete_tag_or_artifact` method; remove the low-level method entirely. The digest-level delete is no longer publicly callable, eliminating the API-surface footgun.                                                                                             |
| Q3  | Helper symmetry           | Apply the same fix in `_delete_harbor_images` (called from `delete_detector`). The footgun does not bite that path today (all versions of one detector are deleted together; `delete_artifact` already absorbs 404), but the semantic is wrong and a future change could regress it. Both callers must route through the safe method. |
| Q4  | Backwards compat          | Breaking change to the `HarborClient` public surface accepted. Caller surface is in-tree (two callers); the change is contained. Aligns with the project's "no backward-compat hacks" rule when the alternative leaves a footgun in the API.                                                                                          |

## 3. Out of scope

- **Image-immutability invariant** (B from brainstorming) — declaring "Harbor is never deleted on user action; retention policy owns all GC". Adjacent platform-level decision; conflating it with this bug fix would muddle the spec. Reopen as its own design if Harbor disk pressure or audit requirements push for it.
- **DB schema / Alembic migration** — no column added or removed; `DetectorVersion.image_digest` semantics unchanged.
- **OpenAPI schema / `frontend/src/api/schema.gen.ts`** — `DELETE /api/v1/detectors/{detector_id}/versions/{tag}` request and response shape unchanged; no regen needed.
- **Frontend UX** — DELETE button (modal confirm, list refetch) unchanged.
- **Real-Harbor integration test** — covered by `respx`-based unit tests modelled on the official Harbor v2 API contract; running against a real Harbor needs the dev cluster and is deferred.
- **Retention policy semantics** — `set_retention_policy` is unaffected; tag-level delete and retention-driven GC remain independent paths.

## 4. Architecture

```
Caller (router or helper)
       │ intent: "delete tag X of detector Y"
       ▼
HarborClient.delete_tag_or_artifact(project, repo, tag, digest)
       │
       ├─[1] GET artifacts/{digest}?with_tag=true → tags
       │     • 404         → return (already gone, idempotent)
       │     • tag ∉ tags  → return (Harbor lost the tag elsewhere)
       │
       ├─[2] len(tags) > 1
       │     → DELETE artifacts/{digest}/tags/{tag}    # tag-level: unpin only
       │
       └─[3] len(tags) == 1
             → DELETE artifacts/{digest}               # digest-level: last tag, manifest GC
```

`delete_tag_or_artifact` is the only method on `HarborClient` that issues a destructive Harbor request after this change. Both callers (`delete_version`, `_delete_harbor_images`) supply both `tag` (the target) and `digest` (already on the DB row). Passing both avoids a tag → digest round-trip and the associated TOCTOU window.

## 5. Components

### 5.1 `HarborClient` — `backend/app/services/harbor.py`

**Add:**

```python
async def delete_tag_or_artifact(
    self, project: str, repo: str, tag: str, digest: str
) -> None:
    """Delete `tag`. Preserve other tags sharing the same manifest.

    Falls back to digest-level delete only when `tag` is the last tag
    on the manifest. Idempotent: missing artifact / missing tag returns
    silently; only genuine network or HTTP errors propagate.
    """
```

Implementation flow follows §4. Uses `with_tag=true` on the artifact GET so the response includes the full tag list in one round-trip. Treats 404 on the GET as "already gone" and returns; treats `tag not in tags` as "another path already removed it" and returns. On the DELETE itself, accepts 200 and 404; raises on other non-2xx responses via `resp.raise_for_status()`.

**Remove:**

```python
async def delete_artifact(self, project: str, repo: str, digest: str) -> None:
    ...
```

After both callers migrate, this method has zero references and is deleted. Removing it makes future digest-level deletes impossible without writing a new method, forcing the choice to be reviewed on its merits.

Other `HarborClient` methods (`ensure_project`, `ensure_robot_account`, `set_retention_policy`, `get_artifact_digest`, `get_scan`, `get_image_labels`, `trigger_scan`) are untouched.

### 5.2 Router callers — `backend/app/routers/detectors.py`

**`delete_version`** (lines 342-419): lines 403-407 swap from `delete_artifact` to `delete_tag_or_artifact`, passing the local `tag` parameter and `version.image_digest`. All surrounding logic — DB-first commit, try/except, `BACKEND_ERRORS{stage="version_delete_harbor"}` metric, `logger.exception` extras — is preserved verbatim.

**`_delete_harbor_images`** (lines 157-193): line 182 swaps similarly, passing `v.git_tag` and `v.image_digest` per loop iteration. The `v.status = DetectorVersionStatus.DELETED` assignment stays inside the try block (only mark deleted after Harbor success). `BACKEND_ERRORS{stage="detector_delete_harbor"}` and the per-version `logger.exception` extras are preserved.

## 6. Error handling

| Situation                                        | `HarborClient` behaviour       | Caller behaviour                     |
| ------------------------------------------------ | ------------------------------ | ------------------------------------ |
| Harbor unreachable / 5xx / timeout               | raises `httpx.HTTPError`       | catch → log + `BACKEND_ERRORS.inc()` |
| Artifact 404 on initial GET                      | silent return                  | not caught                           |
| Tag ∉ artifact's tags                            | silent return                  | not caught                           |
| 401 / 403 (auth)                                 | raises `httpx.HTTPStatusError` | catch → log + metric                 |
| `len(tags) > 1` tag-level DELETE returns 404     | accepts 404                    | not caught                           |
| `len(tags) == 1` digest-level DELETE returns 404 | accepts 404                    | not caught                           |

`HarborClient` absorbs idempotent "already in target state" cases internally; genuine failures (network, auth, 5xx) propagate. Callers retain the existing "DB-first commit, Harbor best-effort" policy — Harbor failures do not surface as user-facing 5xx but leave a metric + log trail for ops to chase.

### 6.1 TOCTOU between GET and DELETE

A small race window exists between the tag-list GET and the DELETE. Two scenarios, both safe:

- **Concurrent build adds a new tag to the same digest.** If the GET observed `len(tags) == 1` and we therefore choose digest-level, Harbor will GC the manifest and the new tag falls with it. This is a race the user instigated by deleting and rebuilding simultaneously; the same race exists in any digest-level GC scheme.
- **Concurrent retag moves the target tag onto a different digest.** Tag-level DELETE on the old digest returns 404 (the tag is no longer attached). We accept the 404; Harbor's actual state matches lolday's intent — the old-digest tag we wanted gone is no longer there, and the new digest (which may carry the same tag name) is correctly untouched.

### 6.2 Loop partial failure (`_delete_harbor_images`)

The helper's pre-existing semantics — set `v.status = DELETED` only after a successful Harbor call, then `await session.commit()` once after the loop — are preserved. A single version's Harbor failure leaves that row at `ACTIVE`; subsequent retries of `delete_detector` re-iterate and reattempt only the unconverged versions. Behavioural parity with the existing helper, just at tag granularity.

`delete_version` differs by design: it commits the DB transition before calling Harbor (see line 393-394). On Harbor failure, the row is already `DELETED` in DB and stays that way. This asymmetry pre-dates this spec and is not changed here.

### 6.3 Metrics & logs

No new label values. Reuses:

- `BACKEND_ERRORS{stage="version_delete_harbor"}` — emitted from `delete_version`.
- `BACKEND_ERRORS{stage="detector_delete_harbor"}` — emitted from `_delete_harbor_images`.
- `logger.exception(...)` with `detector_version_id` / `detector_name` / `tag` extras (already present).

## 7. Testing

### 7.1 `tests/test_services_harbor.py`

`respx`-based unit tests for `HarborClient.delete_tag_or_artifact`:

| Test                                                               | Setup                                                                                                 | Expectation                                                 |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `test_delete_tag_or_artifact_unpins_when_multi_tag`                | `GET artifacts/{digest}?with_tag=true` → `tags: [{"name":"4.1.0"},{"name":"v4.1.0"}]`; tag DELETE 200 | exactly one tag-level DELETE; no digest-level DELETE        |
| `test_delete_tag_or_artifact_falls_back_to_digest_when_last_tag`   | `GET` → `tags: [{"name":"v4.1.0"}]`; digest DELETE 200                                                | one digest-level DELETE (parity with old `delete_artifact`) |
| `test_delete_tag_or_artifact_idempotent_when_artifact_already_404` | `GET` → 404                                                                                           | no DELETE issued; no exception                              |
| `test_delete_tag_or_artifact_silent_when_tag_not_in_artifact`      | `GET` → `tags: [{"name":"v4.0.0"}]`, called with `tag="v4.1.0"`                                       | no DELETE; no exception                                     |
| `test_delete_tag_or_artifact_raises_on_5xx`                        | `GET` → 503                                                                                           | raises `httpx.HTTPStatusError`                              |

**Remove** the existing `test_delete_artifact` (currently at line 56-62) alongside the production method.

### 7.2 `tests/test_routers_detectors.py`

**Add** the regression test for the 2026-05-08 footgun:

```python
async def test_delete_version_only_unpins_target_tag_when_digest_shared(
    async_client, detector_factory, version_factory, auth_owner_headers,
    db_session, monkeypatch,
):
    """Two versions share image_digest. DELETE one tag → other tag survives.

    Regression for 2026-05-08 footgun: digest-level delete used to GC the
    shared manifest, leaving the surviving DB row pointing at Harbor 404.
    """
    detector = await detector_factory(name="rfdet")
    v_old = await version_factory(detector_id=detector.id, git_tag="4.1.0",  image_digest="sha256:abc")
    v_new = await version_factory(detector_id=detector.id, git_tag="v4.1.0", image_digest="sha256:abc")

    fake = FakeHarborWithTags(tags={"sha256:abc": ["4.1.0", "v4.1.0"]})
    monkeypatch.setattr("app.routers.detectors.HarborClient", lambda *a, **k: fake)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/4.1.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    await db_session.refresh(v_new)
    assert v_new.status == DetectorVersionStatus.ACTIVE
    assert fake.calls == [("delete_tag_or_artifact", "detectors", "rfdet", "4.1.0", "sha256:abc")]
    assert fake.tags["sha256:abc"] == ["v4.1.0"]
```

A new `FakeHarborWithTags` helper models the tag-list state so call sites can assert end-state, not just call shape.

**Update** existing tests:

- `test_delete_version_soft_deletes` — adjust the `harbor_calls` assertion to the new tuple shape; FakeHarbor gains tag-state.
- `test_delete_version_returns_204_when_harbor_purge_fails` — same fake-method rename, assertion shape unchanged.

**Untouched** (no Harbor surface): `test_delete_version_blocks_when_in_flight`, `_404_unknown_tag`, `_409_already_deleted`, `_403_non_owner`, `_does_not_break_historical_jobs`.

### 7.3 Out of scope (testing)

- **Real-Harbor integration test.** CI has no Harbor instance; FakeHarbor covers the v2 API contract from the official documentation. Future nightly Harbor smoke is its own decision.
- **Property-based tests.** The branch space (multi-tag / single-tag / 404 / tag-absent) is small; table-driven cases suffice.

## 8. Documentation updates

### 8.1 `docs/architecture.md` §10 — new gotcha entry (#17)

Append:

```markdown
17. **Harbor `image_digest` ≡ manifest GC unit, not tag** — `DetectorVersion.image_digest` maps to Harbor's manifest digest; one manifest can carry multiple tags (BuildKit cache hits on identical content, retag conventions, admin retags). `DELETE /api/v2.0/.../artifacts/{digest}` is digest-level: Harbor GCs the manifest and untags every tag pointing at it. Lolday must always go through `HarborClient.delete_tag_or_artifact(...)`, which reads `with_tag=true` first and uses tag-level `DELETE .../tags/{tag}` whenever more than one tag exists on the artifact. Footgun source: 2026-05-08 (`4.1.0` and `v4.1.0` shared a digest after a retag-convention change; digest-level delete pulled both). Fixed in PR #<TBD>.
```

§9 (tech debt) is unaffected.

### 8.2 `docs/runbooks/troubleshooting.md` — recovery procedure

Append a new section under the existing Harbor / images grouping:

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
````

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

### 8.3 No changes elsewhere

`README.md`, `docs/conventions.md`, and `.claude/rules/backend.md` are unchanged. The new tag-level delete pattern is self-evident from the codebase after the fix; no rule entry is needed to remember it.

## 9. Release & deploy

### 9.1 PR

- Branch: `fix/detector-version-tag-level-delete`
- Title: `fix(detectors): tag-level Harbor delete prevents shared-digest GC footgun`
- Body links: this spec + the implementation plan (`docs/superpowers/plans/2026-05-08-detector-version-delete-tag-level.md`).
- CI gates: `lint`, `backend`, `frontend`, `helm`, `images`, `helpers` — all green before squash-merge.

### 9.2 Release cut

After PR merge:

```bash
# bump charts/lolday/Chart.yaml version + appVersion to 0.20.7
# bump backend/pyproject.toml version
# commit chore(release): cut v0.20.7
git tag v0.20.7
git push --tags
````

`images.yml` produces `ghcr.io/bolin8017/lolday-backend:v0.20.7` (and the semver derivatives `0.20.7`, `0.20`, `0`, `latest`). Helpers / frontend / mlflow-server / pytorch-cu12-base unchanged from v0.20.6.

### 9.3 Cluster rollout

Backend-only image change. Minimal patch path keeps other components stable and avoids `helm upgrade` re-rolling unrelated state:

```bash
kubectl -n lolday set image deploy/backend backend=harbor.lolday.svc:80/lolday/backend:v0.20.7
kubectl -n lolday rollout status deploy/backend
```

Or run `bash scripts/deploy.sh` if the chart needs other in-tree changes alongside the backend bump.

### 9.4 Smoke test (post-deploy)

Reproduce the original shared-digest scenario against the live cluster:

1. Build any detector at `v4.1.0` → Harbor has `v4.1.0`.
2. Retag in Harbor (API or GUI) so a second tag (`cleanup-test`) shares the digest.
3. `DELETE /api/v1/detectors/{id}/versions/cleanup-test` → 204.
4. Verify:
   - `docker pull harbor.lolday.svc:80/detectors/<name>:v4.1.0` succeeds.
   - DB row for `v4.1.0` is still `ACTIVE`.
   - `docker pull ...:cleanup-test` returns 404.
5. Submit a train job using the `v4.1.0` model — vcjob progresses normally.
6. Final cleanup: `DELETE .../versions/v4.1.0` — last tag remaining, falls through to digest-level delete; Harbor and DB both clean.

## 10. References

- Bug report: this conversation, 2026-05-08 (rfdet/elf-rf retag from `4.1.0` → `v4.1.0`).
- Recent precedent — submit-job platform stance + footgun eradication: `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md` (#114, #115).
- Harbor v2 API:
  - Digest-level delete: `DELETE /api/v2.0/projects/{p}/repositories/{r}/artifacts/{digest}`
  - Tag-level delete: `DELETE /api/v2.0/projects/{p}/repositories/{r}/artifacts/{digest}/tags/{tag}`
  - Tag-list read: `GET /api/v2.0/projects/{p}/repositories/{r}/artifacts/{digest}?with_tag=true`
- OCI Distribution Spec — manifest is the GC unit; tags are mutable references to manifests.
- Architecture context: `docs/architecture.md` §1.2 (deploy-platform stance), §5.3 (Harbor URL conventions), §10 (gotchas index).
