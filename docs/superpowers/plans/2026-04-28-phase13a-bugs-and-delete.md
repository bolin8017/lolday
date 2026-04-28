# Phase 13a Bug Fixes, Delete, Layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five user-visible defects (View manifest, build logs empty, sidebar layout, missing delete UX, evaluate metrics empty) at root cause.

**Architecture:** Five small independent areas. A1 makes `VersionDetailRead.manifest` nullable and adds a frontend null-state. A2 refactors `_capture_log_tail` / `_capture_job_log_tail` into one generic helper that picks the failing container based on `failure_reason`. A3 converts `_authed.tsx` to a fixed-viewport app shell so the sidebar's bottom block always stays in viewport. A4 adds `DetectorVersionStatus.DELETED` enum value, a new `DELETE .../versions/{tag}` endpoint, in-flight job protection on both delete endpoints, and a GitHub-style typing-name confirmation dialog. A5 is an investigation task (reproduce → diagnose → fix at the broken link in the events pipeline).

**Tech Stack:** FastAPI, SQLAlchemy 2 / PostgreSQL, Alembic, React 18 + TypeScript + Tailwind + shadcn/ui + react-router 7, vitest + playwright, pytest.

**Spec:** `/home/bolin8017/Documents/repositories/lolday/docs/superpowers/specs/2026-04-28-phase13a-bugs-and-delete-design.md`

---

## File Structure (which file does what)

### Backend
- `backend/app/schemas/detector.py` — `VersionDetailRead.manifest: dict[str, Any] | None` (A1)
- `backend/app/reconciler.py` — refactor log-capture functions (A2)
- `backend/app/models/detector.py` — `DetectorVersionStatus.DELETED` (A4)
- `backend/app/routers/detectors.py` — new `DELETE .../versions/{tag}`; strengthen existing detector delete (A4)
- `backend/migrations/versions/<hash>_phase13a_detector_version_deleted.py` — enum value add (A4)
- `backend/tests/test_routers_detectors.py` — A1 + A4 cases
- `backend/tests/test_reconciler_log_capture.py` — A2 cases (new file)

### Frontend
- `frontend/src/routes/_authed.tsx` — fixed-viewport app shell (A3)
- `frontend/src/routes/_authed.detectors.$id.tsx` — null-manifest fallback + per-version delete button + detector delete button (A1, A4)
- `frontend/src/routes/_authed.detectors._index.tsx` — row dropdown menu with Delete (A4)
- `frontend/src/api/queries/detectors.ts` — `useDeleteVersion` hook + invalidation strengthening on `useDeleteDetector` (A4)
- `frontend/src/components/common/DeleteConfirmDialog.tsx` — new reusable typing-name dialog (A4)
- `frontend/src/components/ui/dropdown-menu.tsx` — verify exists (shadcn) or add (A4)
- `frontend/tests/e2e/detectors.spec.ts` — A1 + A4 e2e
- `frontend/tests/e2e/layout.spec.ts` — A3 e2e (new file)
- `frontend/tests/unit/DeleteConfirmDialog.test.tsx` — A4 unit (new file)

### Configuration / docs
- (no chart changes for 13a)

---

## Task 1.1: Make `VersionDetailRead.manifest` nullable

**Files:**
- Modify: `backend/app/schemas/detector.py`
- Modify: `backend/tests/test_routers_detectors.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routers_detectors.py`:

```python
async def test_get_version_legacy_null_manifest_returns_200(
    async_client, detector_factory, version_factory, auth_owner_headers
):
    """Phase 13a A1: legacy versions built before maldet 1.1 have manifest=NULL.

    Schema must accept None; endpoint must return 200 with `manifest: null`.
    """
    detector = await detector_factory(name="legacy-det")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v0.1.0",
        manifest=None,            # ← legacy build, NULL in DB
    )
    resp = await async_client.get(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["manifest"] is None
    assert body["git_tag"] == "v0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_routers_detectors.py::test_get_version_legacy_null_manifest_returns_200 -xvs
```

Expected: FAIL with `pydantic.ValidationError: ... manifest ... none is not an allowed value` (HTTP 500).

- [ ] **Step 3: Apply the schema change**

In `backend/app/schemas/detector.py`, change:

```python
class VersionDetailRead(VersionRead):
    manifest: dict[str, Any]   # phase 11e — full maldet 1.1 manifest (JSONB column)
```

to:

```python
class VersionDetailRead(VersionRead):
    # Phase 13a (A1): nullable for legacy versions built before maldet 1.1
    # whose `manifest` JSONB column is NULL. Frontend renders a fallback.
    manifest: dict[str, Any] | None
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && uv run pytest tests/test_routers_detectors.py::test_get_version_legacy_null_manifest_returns_200 -xvs
```

Expected: PASS.

Also run the full detector router test file to verify nothing regressed:

```
cd backend && uv run pytest tests/test_routers_detectors.py -xvs
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/detector.py backend/tests/test_routers_detectors.py
git commit -m "$(cat <<'EOF'
fix(detectors): make VersionDetailRead.manifest nullable for legacy versions (phase 13a A1)

Pre-phase-11e versions have manifest=NULL in DB; the previously required
field caused 500 in get_version. Frontend now renders a null-state
fallback (next commit). Adds regression test.
EOF
)"
```

---

## Task 1.2: Frontend ManifestView null-state fallback

**Files:**
- Modify: `frontend/src/routes/_authed.detectors.$id.tsx`
- Modify: `frontend/tests/e2e/detectors.spec.ts`

- [ ] **Step 1: Regenerate frontend schema types**

The backend schema change must propagate to `frontend/src/api/schema.gen.ts`. Run the existing schema regeneration script:

```bash
cd frontend && pnpm gen:schema
```

Expected: `schema.gen.ts` updated; `manifest` field becomes `{ [key: string]: unknown } | null`.

If `pnpm gen:schema` doesn't exist, check `frontend/package.json` scripts. The phase 11e workflow regenerates by running backend, dumping `/openapi.json`, and using `openapi-typescript`. Use the same procedure documented in any recent phase plan.

- [ ] **Step 2: Write the failing playwright test**

Add to `frontend/tests/e2e/detectors.spec.ts`:

```ts
test("View manifest button opens Sheet with fallback for legacy version", async ({ page, seedLegacyVersion }) => {
  const detector = await seedLegacyVersion({ name: "legacy-det", tag: "v0.1.0" });
  await page.goto(`/detectors/${detector.id}`);
  await page.getByRole("tab", { name: /versions/i }).click();
  await page.getByRole("button", { name: /view manifest/i }).first().click();

  // Sheet should be visible
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();

  // Fallback text for null manifest
  await expect(sheet.getByText(/legacy build/i)).toBeVisible();
  await expect(sheet.getByText(/rebuild this version/i)).toBeVisible();
});

test("View manifest button opens Sheet with manifest tree for phase11e+ version", async ({ page, seedActiveVersion }) => {
  const detector = await seedActiveVersion({ name: "modern-det", tag: "v3.0.0" });
  await page.goto(`/detectors/${detector.id}`);
  await page.getByRole("tab", { name: /versions/i }).click();
  await page.getByRole("button", { name: /view manifest/i }).first().click();

  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  // Manifest content (from JSON tree) — at least the detector name should appear
  await expect(sheet.getByText("modern-det")).toBeVisible();
});
```

`seedLegacyVersion` and `seedActiveVersion` are test fixtures: write them in the existing playwright fixtures file (`frontend/tests/e2e/fixtures.ts` or equivalent — match existing pattern). They POST seed data via backend admin API or directly via test database. Refer to existing playwright tests in `frontend/tests/e2e/` for the seeding pattern.

- [ ] **Step 3: Run tests to verify they fail**

```
cd frontend && pnpm playwright test detectors.spec.ts -g "View manifest"
```

Expected: legacy test fails (current code shows "Failed to load manifest"); modern test may pass already.

- [ ] **Step 4: Implement the null-state fallback**

In `frontend/src/routes/_authed.detectors.$id.tsx`, replace `<ManifestView>` (currently around line 172):

```tsx
function ManifestView({ detectorId, tag }: { detectorId: string; tag: string }) {
  const { data, isLoading, error } = useDetectorVersion(detectorId, tag);
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error) return <p className="text-sm text-destructive">Failed to load manifest.</p>;
  const manifest = data?.manifest;
  if (manifest == null) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-destructive">
          Version has no manifest (legacy build).
        </p>
        <p className="text-muted-foreground">
          Rebuild this version with maldet ≥ 1.1 to see the typed manifest.
        </p>
      </div>
    );
  }
  return <JsonViewer value={manifest} />;
}
```

(Note: `<JsonViewer>` here will be replaced by `<JsonTreeView>` in Phase 13b. For 13a we keep the existing component.)

- [ ] **Step 5: Run tests to verify they pass**

```
cd frontend && pnpm playwright test detectors.spec.ts -g "View manifest"
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/_authed.detectors.$id.tsx frontend/src/api/schema.gen.ts frontend/tests/e2e/detectors.spec.ts
git commit -m "$(cat <<'EOF'
fix(detectors): null-state fallback when manifest is missing (phase 13a A1)

Pairs with the backend nullable schema change. Legacy versions now show
a clear 'rebuild with maldet >= 1.1' message instead of a generic
'Failed to load manifest' error. Sheet itself opens correctly in both
legacy and modern cases.
EOF
)"
```

---

## Task 2.1: `_capture_pod_logs` generic helper with tests

**Files:**
- Modify: `backend/app/reconciler.py`
- Create: `backend/tests/test_reconciler_log_capture.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reconciler_log_capture.py`:

```python
"""Phase 13a A2: log capture from build / job pods with init-container fallback."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from kubernetes.client import ApiException

from app.reconciler import _capture_pod_logs


@pytest.fixture
def mock_k8s_pod():
    """Returns a mock pod whose .metadata.name is fixed."""
    pod = MagicMock()
    pod.metadata.name = "test-pod-abc"
    return pod


def _make_v1(pod, log_responses):
    """Build a mocked core_v1() that returns `pod` for list and dispatches
    log reads from `log_responses` (dict container_name -> str | ApiException)."""
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[pod])

    def read_log(name, namespace, container, tail_lines):
        result = log_responses.get(container)
        if isinstance(result, ApiException):
            raise result
        return result or ""
    v1.read_namespaced_pod_log.side_effect = read_log
    return v1


@pytest.mark.asyncio
async def test_capture_pod_logs_main_container_success(mock_k8s_pod):
    """Happy path: main container has logs, returned as-is."""
    v1 = _make_v1(mock_k8s_pod, {"buildkit": "BUILD OUTPUT\nfinal layer pushed"})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert "BUILD OUTPUT" in result
    assert "final layer pushed" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_falls_back_to_init_when_main_missing(mock_k8s_pod):
    """Build failed in init container; main never started → 404. Should walk
    back through init containers and return the first one with logs."""
    v1 = _make_v1(mock_k8s_pod, {
        "buildkit": ApiException(status=400, reason="container 'buildkit' not found"),
        "validate": ApiException(status=400, reason="not found"),
        "clone": "fatal: could not read from remote repository",
    })
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert "[clone]" in result   # header marks which container the log came from
    assert "could not read from remote" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_uses_failure_reason_hint(mock_k8s_pod):
    """When failure_reason names a container ('validate_failed: ...'),
    that container is queried first."""
    call_order = []
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[mock_k8s_pod])

    def read_log(name, namespace, container, tail_lines):
        call_order.append(container)
        if container == "validate":
            return "ValidationError: maldet.toml missing [project] section"
        raise ApiException(status=400, reason="not found")
    v1.read_namespaced_pod_log.side_effect = read_log

    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason="validate_failed: exit=2",
            tail_bytes=1024,
        )
    assert call_order[0] == "validate"
    assert "[validate]" in result
    assert "missing [project]" in result


@pytest.mark.asyncio
async def test_capture_pod_logs_returns_empty_when_all_fail(mock_k8s_pod):
    """All container reads 404 → return empty string."""
    v1 = _make_v1(mock_k8s_pod, {
        c: ApiException(status=400, reason="not found")
        for c in ("buildkit", "clone", "validate")
    })
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert result == ""


@pytest.mark.asyncio
async def test_capture_pod_logs_no_pod_returns_empty(mock_k8s_pod):
    """list_namespaced_pod returns no items → empty."""
    v1 = MagicMock()
    v1.list_namespaced_pod.return_value = MagicMock(items=[])
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=1024,
        )
    assert result == ""


@pytest.mark.asyncio
async def test_capture_pod_logs_truncates_to_tail_bytes(mock_k8s_pod):
    """tail_bytes truncates the result so we don't blow log_tail column."""
    v1 = _make_v1(mock_k8s_pod, {"buildkit": "X" * 10_000})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_pod_logs(
            namespace="test-ns",
            label_selector="lolday.io/build-id=xyz",
            main_container="buildkit",
            init_containers=("clone", "validate"),
            failure_reason=None,
            tail_bytes=8192,
        )
    assert len(result) <= 8192
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_reconciler_log_capture.py -xvs
```

Expected: ImportError or AttributeError — `_capture_pod_logs` doesn't exist yet.

- [ ] **Step 3: Implement `_capture_pod_logs`**

Add to `backend/app/reconciler.py` (place near the existing `_capture_log_tail`, around line 600):

```python
def _container_from_failure_reason(failure_reason: str | None) -> str | None:
    """Extract container name from a failure_reason string like 'clone_failed: exit=1'."""
    if not failure_reason:
        return None
    head = failure_reason.split(":", 1)[0].strip()
    if head.endswith("_failed"):
        return head.removesuffix("_failed")
    return None


async def _capture_pod_logs(
    *,
    namespace: str,
    label_selector: str,
    main_container: str,
    init_containers: tuple[str, ...],
    failure_reason: str | None,
    tail_bytes: int,
    tail_lines: int = 200,
) -> str:
    """Capture log tail from the failing or main container of a labeled pod.

    Phase 13a A2: previous implementations hard-coded the container name
    (kaniko vs buildkit; detector only) and could not surface init-container
    output when the build/job failed before main started. This generic
    helper:
      1. Tries the container hinted by failure_reason first (e.g.
         'validate_failed' → 'validate').
      2. Falls back to main_container.
      3. Falls back to each init_container in order.
      4. Concatenates whatever logs were retrievable, prefixed with a
         '[<container>]' header line so the reader can tell what's what.
      5. Returns "" if no logs are retrievable from any container.

    The result is truncated to `tail_bytes` from the end so the persisted
    log_tail column doesn't blow up.
    """
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=namespace, label_selector=label_selector,
        )
    except ApiException:
        return ""
    if not pods.items:
        return ""
    pod = pods.items[0]

    # Build the container query order
    hinted = _container_from_failure_reason(failure_reason)
    order: list[str] = []
    if hinted and (hinted == main_container or hinted in init_containers):
        order.append(hinted)
    if main_container not in order:
        order.append(main_container)
    for ic in init_containers:
        if ic not in order:
            order.append(ic)

    # Try each container in order; collect what we can.
    chunks: list[str] = []
    for container in order:
        try:
            log = core_v1().read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
        except ApiException:
            continue
        if log:
            chunks.append(f"[{container}]\n{log}")

    if not chunks:
        return ""
    combined = "\n\n".join(chunks)
    return combined[-tail_bytes:]
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_reconciler_log_capture.py -xvs
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/reconciler.py backend/tests/test_reconciler_log_capture.py
git commit -m "$(cat <<'EOF'
feat(reconciler): generic _capture_pod_logs helper with init-container fallback (phase 13a A2)

Builds and jobs share the same failure mode: when the failure happens in
an init container, the main container never starts and main-only log
capture returns empty. This helper picks the hinted container first
(from failure_reason), falls back to main, then walks init containers in
order, and concatenates whatever it finds with [container] headers.

Wires up in next commit.
EOF
)"
```

---

## Task 2.2: Refactor `_capture_log_tail` and `_capture_job_log_tail` to use the helper

**Files:**
- Modify: `backend/app/reconciler.py`

- [ ] **Step 1: Write a regression test verifying the build helper picks `buildkit`**

Add to `backend/tests/test_reconciler_log_capture.py`:

```python
@pytest.mark.asyncio
async def test_capture_log_tail_uses_buildkit_container(mock_k8s_pod):
    """Regression: previous code looked for 'kaniko' which didn't exist,
    so log_tail was always empty for real builds."""
    from app.reconciler import _capture_log_tail
    from app.models.detector import DetectorBuild

    build = MagicMock(spec=DetectorBuild)
    build.id = uuid4()
    build.failure_reason = None

    v1 = _make_v1(mock_k8s_pod, {"buildkit": "buildctl-daemonless: pushed sha256:abc"})
    with patch("app.reconciler.core_v1", return_value=v1):
        result = await _capture_log_tail(build)
    assert "pushed sha256:abc" in result
    assert "[buildkit]" in result
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_reconciler_log_capture.py::test_capture_log_tail_uses_buildkit_container -xvs
```

Expected: FAIL — current `_capture_log_tail` reads `container="kaniko"`, which our mock doesn't have, so result is empty (no `[buildkit]` header).

- [ ] **Step 3: Refactor both functions**

In `backend/app/reconciler.py`, replace `_capture_log_tail` (around line 603) and `_capture_job_log_tail` (around line 1071) with thin wrappers:

```python
async def _capture_log_tail(b: DetectorBuild) -> str:
    """Capture build pod's log tail.

    Phase 13a A2: was hard-coded to container='kaniko' (wrong — actual
    name is 'buildkit'). Now uses the generic helper with init-container
    fallback for when builds fail in clone/validate.
    """
    return await _capture_pod_logs(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
        main_container="buildkit",
        init_containers=("clone", "validate"),
        failure_reason=b.failure_reason,
        tail_bytes=settings.BUILD_LOG_TAIL_BYTES,
    )


async def _capture_job_log_tail(j: Job) -> str:
    """Capture job pod's log tail.

    Phase 13a A2: previously read main 'detector' container only. Now
    also captures init-container logs (config-writer, model-fetcher) when
    the job fails before main starts.
    """
    return await _capture_pod_logs(
        namespace=settings.JOB_NAMESPACE,
        label_selector=f"lolday.job-id={j.id}",
        main_container="detector",
        init_containers=("config-writer", "model-fetcher"),
        failure_reason=j.failure_reason,
        tail_bytes=8192,
    )
```

- [ ] **Step 4: Run all reconciler tests**

```
cd backend && uv run pytest tests/test_reconciler_log_capture.py -xvs
cd backend && uv run pytest tests/ -k reconciler -xvs
```

Expected: all PASS. The reconciler integration tests should not regress.

- [ ] **Step 5: Commit**

```bash
git add backend/app/reconciler.py backend/tests/test_reconciler_log_capture.py
git commit -m "$(cat <<'EOF'
fix(reconciler): wire _capture_log_tail/_capture_job_log_tail through generic helper (phase 13a A2)

Build container is 'buildkit' (rootless BuildKit), not 'kaniko' — fixing
the long-standing 'Logs are empty' UI bug. Job log capture now also
surfaces init-container output (config-writer, model-fetcher) so
failures before the main 'detector' container starts are visible in the
UI.
EOF
)"
```

---

## Task 3.1: Sidebar layout fix (fixed-viewport app shell)

**Files:**
- Modify: `frontend/src/routes/_authed.tsx`
- Create: `frontend/tests/e2e/layout.spec.ts`

- [ ] **Step 1: Write the failing playwright tests**

Create `frontend/tests/e2e/layout.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

test.describe("App-shell layout — sidebar bottom block always visible", () => {
  test("logout button visible on /jobs even with long list", async ({ page, seedManyJobs }) => {
    await seedManyJobs(80);
    await page.goto("/jobs");
    await page.waitForSelector("h1");

    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
    expect(await logout.boundingBox()).not.toBeNull();
    const box = (await logout.boundingBox())!;
    const viewportHeight = page.viewportSize()!.height;
    expect(box.y + box.height).toBeLessThanOrEqual(viewportHeight + 1);
  });

  test("logout button visible on /runs", async ({ page, seedManyRuns }) => {
    await seedManyRuns(40);
    await page.goto("/runs");
    await page.waitForSelector("h1");

    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
  });

  test("body does not scroll; only main scrolls", async ({ page, seedManyJobs }) => {
    await seedManyJobs(80);
    await page.goto("/jobs");

    const bodyScrolls = await page.evaluate(
      () => document.documentElement.scrollHeight > window.innerHeight + 1,
    );
    expect(bodyScrolls).toBe(false);

    const mainScrolls = await page.evaluate(() => {
      const main = document.querySelector("main");
      return main ? main.scrollHeight > main.clientHeight : false;
    });
    expect(mainScrolls).toBe(true);
  });

  test("logout still visible on short pages (regression)", async ({ page }) => {
    await page.goto("/detectors");
    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
  });
});
```

`seedManyJobs` and `seedManyRuns` are test fixtures — add them to the existing fixtures file. They populate the test DB with N jobs/runs.

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend && pnpm playwright test layout.spec.ts
```

Expected: 3 of 4 fail (the "long list" cases). The "short page" case may pass already.

- [ ] **Step 3: Apply the layout fix**

In `frontend/src/routes/_authed.tsx`, change the layout JSX:

```tsx
return (
  <div className="flex h-screen overflow-hidden">
    <Sidebar />
    <div className="flex flex-1 flex-col overflow-hidden">
      <TopBar />
      <main className="flex-1 overflow-y-auto bg-background p-6">
        <Outlet />
      </main>
    </div>
  </div>
);
```

The two changes:
- `min-h-screen` → `h-screen overflow-hidden` on the parent.
- `flex flex-1 flex-col` → `flex flex-1 flex-col overflow-hidden` on the middle column.
- `overflow-auto` → `overflow-y-auto` on `<main>` (explicit Y-only scroll).

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && pnpm playwright test layout.spec.ts
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.tsx frontend/tests/e2e/layout.spec.ts
git commit -m "$(cat <<'EOF'
fix(layout): fixed-viewport app shell so sidebar bottom block stays in view (phase 13a A3)

Was 'min-h-screen' on the parent, which let it grow past viewport when
job/run list was long; sidebar grew with it and pushed profile/logout
below the fold. Now h-screen + overflow-hidden on parent + middle col,
overflow-y-auto only on main — standard SaaS app-shell pattern (Vercel,
GitHub, Linear).
EOF
)"
```

---

## Task 4.1: Migration — add `DetectorVersionStatus.DELETED` enum value

**Files:**
- Modify: `backend/app/models/detector.py`
- Create: `backend/migrations/versions/<hash>_phase13a_detector_version_deleted.py`

- [ ] **Step 1: Add the enum value to the model**

In `backend/app/models/detector.py`, find the `DetectorVersionStatus` enum and add `DELETED`:

```python
class DetectorVersionStatus(str, Enum):
    ACTIVE = "active"
    RETENTION_PRUNED = "retention_pruned"   # GC by reconciler retention
    DELETED = "deleted"                      # Phase 13a (A4): user-initiated soft delete
```

- [ ] **Step 2: Generate the Alembic revision**

```bash
cd backend && uv run alembic revision -m "phase13a detector version deleted enum"
```

This creates a new file in `backend/migrations/versions/`. Note its hash (e.g., `7b1c2a3d4e5f`).

- [ ] **Step 3: Write the migration body**

Replace the auto-generated `upgrade()` and `downgrade()` with:

```python
"""phase13a detector version deleted enum

Revision ID: <hash>
Revises: <previous-revision-id>     # autopopulated; verify it points at the most recent migration
Create Date: 2026-04-28 ...
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "<hash>"             # auto-filled by alembic revision
down_revision = "<previous>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL ALTER TYPE ADD VALUE cannot run inside a transaction. Alembic's
    # default transaction mode would error; this migration runs autocommit per
    # PostgreSQL's documented constraint.
    op.execute("COMMIT")
    op.execute("ALTER TYPE detectorversionstatus ADD VALUE IF NOT EXISTS 'deleted'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the
    # type. Phase 13a accepts forward-only per spec authorization. Existing
    # rows with status='deleted' would block any type recreation, so even the
    # heroic recreate-and-rename approach is unsafe in practice.
    pass
```

If `down_revision` was auto-populated correctly, leave it. Otherwise look at the previous most-recent migration filename and copy its revision id.

- [ ] **Step 4: Run the migration against a clean test DB**

```bash
cd backend && uv run alembic upgrade head
```

Expected: no errors, head moves forward. If using a real local DB, verify with:

```bash
psql -d lolday -c "SELECT enum_range(NULL::detectorversionstatus);"
```

Expected output includes `deleted`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/detector.py backend/migrations/versions/<hash>_phase13a_detector_version_deleted.py
git commit -m "$(cat <<'EOF'
feat(detector): add DetectorVersionStatus.DELETED for user-initiated delete (phase 13a A4)

Distinct from RETENTION_PRUNED (reconciler GC). The DELETE endpoint
introduced in the next commit sets this status instead of removing the
DB row, preserving FK integrity for historical jobs.

Migration is forward-only per spec; PostgreSQL enum value cannot be
removed without recreating the type.
EOF
)"
```

---

## Task 4.2: Backend — `DELETE /detectors/{id}/versions/{tag}` endpoint

**Files:**
- Modify: `backend/app/routers/detectors.py`
- Modify: `backend/tests/test_routers_detectors.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routers_detectors.py`:

```python
async def test_delete_version_soft_deletes(
    async_client, detector_factory, version_factory, auth_owner_headers,
    monkeypatch,
):
    """Happy path: soft-deletes the version and best-effort purges Harbor."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        image_digest="sha256:abc",
    )

    harbor_calls = []

    class FakeHarbor:
        def __init__(self, *a, **kw): pass
        async def delete_artifact(self, project, repo, digest):
            harbor_calls.append((project, repo, digest))

    monkeypatch.setattr("app.routers.detectors.HarborClient", FakeHarbor)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    # Refetch via DB or list endpoint — version should be hidden from active list
    list_resp = await async_client.get(
        f"/api/v1/detectors/{detector.id}/versions", headers=auth_owner_headers,
    )
    assert all(v["git_tag"] != "v1.0.0" for v in list_resp.json()["items"])
    assert harbor_calls == [("detectors", "rfdet", "sha256:abc")]


async def test_delete_version_blocks_when_in_flight(
    async_client, detector_factory, version_factory, job_factory,
    auth_owner_headers,
):
    """409 when any job using this version is non-terminal."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    await job_factory(detector_version_id=version.id, status="running")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "version_has_in_flight_jobs"


async def test_delete_version_404_unknown_tag(
    async_client, detector_factory, auth_owner_headers,
):
    detector = await detector_factory(name="rfdet")
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/nonexistent",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 404


async def test_delete_version_409_already_deleted(
    async_client, detector_factory, version_factory, auth_owner_headers,
):
    detector = await detector_factory(name="rfdet")
    await version_factory(
        detector_id=detector.id, git_tag="v1.0.0", status="deleted",
    )
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "version_not_active"


async def test_delete_version_403_non_owner(
    async_client, detector_factory, version_factory, auth_other_user_headers,
):
    detector = await detector_factory(name="rfdet")  # owned by `owner`
    await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_other_user_headers,
    )
    assert resp.status_code == 403


async def test_delete_version_does_not_break_historical_jobs(
    async_client, detector_factory, version_factory, job_factory,
    auth_owner_headers, monkeypatch,
):
    """After delete, GET /jobs/{historical_job_id} still succeeds and
    references the deleted version row."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    job = await job_factory(detector_version_id=version.id, status="succeeded")

    # No-op Harbor for this test
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    job_resp = await async_client.get(
        f"/api/v1/jobs/{job.id}", headers=auth_owner_headers,
    )
    assert job_resp.status_code == 200
    assert job_resp.json()["detector_version_id"] == str(version.id)
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_routers_detectors.py -k delete_version -xvs
```

Expected: ALL FAIL with 405 Method Not Allowed (endpoint doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/routers/detectors.py` after the existing `delete_detector` endpoint:

```python
NON_TERMINAL_JOB_STATUSES = (
    "pending", "preparing", "queued", "running",
)


@router.delete("/{detector_id}/versions/{tag}", status_code=204)
async def delete_version(
    tag: str,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Soft-delete a single detector version. Phase 13a A4.

    Sets `DetectorVersionStatus.DELETED`, best-effort purges the Harbor
    artifact, and returns 204. Returns 409 if any job using this version
    is non-terminal.

    Historical jobs that reference the deleted version row remain
    queryable; the FK is intact (we never DROP the row).
    """
    from app.models.job import Job

    res = await session.execute(
        select(DetectorVersion).where(
            DetectorVersion.detector_id == detector.id,
            DetectorVersion.git_tag == tag,
        )
    )
    version = res.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")

    if version.status != DetectorVersionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail={
            "code": "version_not_active",
            "message": f"version is in status {version.status.value}, cannot delete",
        })

    in_flight = await session.execute(
        select(Job.id).where(
            Job.detector_version_id == version.id,
            Job.status.in_(NON_TERMINAL_JOB_STATUSES),
        ).limit(1)
    )
    if in_flight.scalar_one_or_none():
        raise HTTPException(status_code=409, detail={
            "code": "version_has_in_flight_jobs",
            "message": "Cancel running jobs that use this version before deleting it.",
        })

    version.status = DetectorVersionStatus.DELETED
    await session.commit()

    if settings.HARBOR_ADMIN_PASSWORD:
        try:
            harbor = HarborClient(
                settings.HARBOR_URL,
                settings.HARBOR_ADMIN_USERNAME,
                settings.HARBOR_ADMIN_PASSWORD,
            )
            await harbor.delete_artifact(
                "detectors", detector.name, version.image_digest,
            )
        except Exception:
            BACKEND_ERRORS.labels(stage="version_delete_harbor").inc()
            logger.exception(
                "harbor purge on version soft-delete failed",
                extra={
                    "detector_version_id": str(version.id),
                    "detector_name": detector.name,
                    "tag": tag,
                },
            )

    return Response(status_code=204)
```

If `Job` and `JobStatus` are not yet imported in `detectors.py`, also add:

```python
# at top of file, with the other model imports
from app.models.job import Job
```

`NON_TERMINAL_JOB_STATUSES` uses string values. Cross-check `frontend/src/lib/status.ts` to confirm matching strings; align with `JobStatus` enum values.

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_routers_detectors.py -k delete_version -xvs
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/detectors.py backend/tests/test_routers_detectors.py
git commit -m "$(cat <<'EOF'
feat(detectors): DELETE /detectors/{id}/versions/{tag} (phase 13a A4)

Soft-deletes a single version (status -> DELETED), best-effort purges
Harbor artifact. Blocks (409) when any non-terminal job references the
version. Historical jobs continue to resolve the FK so audit trail is
preserved.
EOF
)"
```

---

## Task 4.3: Backend — strengthen `DELETE /detectors/{id}` with in-flight check

**Files:**
- Modify: `backend/app/routers/detectors.py`
- Modify: `backend/tests/test_routers_detectors.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routers_detectors.py`:

```python
async def test_delete_detector_blocks_when_in_flight(
    async_client, detector_factory, version_factory, job_factory,
    auth_owner_headers,
):
    """Existing DELETE /detectors/{id} now blocks if any of its versions
    has a non-terminal job. Phase 13a A4."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    await job_factory(detector_version_id=version.id, status="running")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}", headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "detector_has_in_flight_jobs"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_routers_detectors.py::test_delete_detector_blocks_when_in_flight -xvs
```

Expected: FAIL with 204 (current endpoint succeeds without the check).

- [ ] **Step 3: Add the check**

In `backend/app/routers/detectors.py`, modify `delete_detector` (around line 294). Insert the in-flight check before any state mutation:

```python
@router.delete("/{detector_id}", status_code=204)
async def delete_detector(
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    from app.models.job import Job

    in_flight = await session.execute(
        select(Job.id)
        .join(DetectorVersion, Job.detector_version_id == DetectorVersion.id)
        .where(
            DetectorVersion.detector_id == detector.id,
            Job.status.in_(NON_TERMINAL_JOB_STATUSES),
        ).limit(1)
    )
    if in_flight.scalar_one_or_none():
        raise HTTPException(status_code=409, detail={
            "code": "detector_has_in_flight_jobs",
            "message": "Cancel running jobs for this detector before deleting it.",
        })

    detector_name = detector.name
    detector_id = detector.id
    detector.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    # ... existing Harbor cleanup code unchanged ...
    try:
        await _delete_harbor_images(detector_name, session, detector_id)
    except Exception:
        BACKEND_ERRORS.labels(stage="harbor_image_cleanup").inc()
        logger.exception(
            "harbor image cleanup on soft-delete failed",
            extra={"detector_id": str(detector_id), "detector_name": detector_name},
        )
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_routers_detectors.py -k delete_detector -xvs
```

Expected: all PASS, including pre-existing happy-path tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/detectors.py backend/tests/test_routers_detectors.py
git commit -m "$(cat <<'EOF'
feat(detectors): block detector delete on in-flight jobs (phase 13a A4)

Symmetric protection with the new per-version delete: returns 409
detector_has_in_flight_jobs if any version has a non-terminal job.
EOF
)"
```

---

## Task 4.4: Frontend — `<DeleteConfirmDialog>` component

**Files:**
- Create: `frontend/src/components/common/DeleteConfirmDialog.tsx`
- Create: `frontend/tests/unit/DeleteConfirmDialog.test.tsx`
- Verify: `frontend/src/components/ui/dialog.tsx` (shadcn) — already exists per Phase 5

- [ ] **Step 1: Write failing unit tests**

Create `frontend/tests/unit/DeleteConfirmDialog.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";

describe("DeleteConfirmDialog", () => {
  function setup(overrides: Partial<React.ComponentProps<typeof DeleteConfirmDialog>> = {}) {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    const props: React.ComponentProps<typeof DeleteConfirmDialog> = {
      open: true,
      onOpenChange,
      title: "Delete detector elfrfdet?",
      description: "This will purge Harbor images.",
      confirmText: "elfrfdet",
      onConfirm,
      pending: false,
      errorBanner: null,
      ...overrides,
    };
    render(<DeleteConfirmDialog {...props} />);
    return { onConfirm, onOpenChange };
  }

  it("renders title and description", () => {
    setup();
    expect(screen.getByText(/Delete detector elfrfdet/)).toBeInTheDocument();
    expect(screen.getByText(/purge Harbor images/)).toBeInTheDocument();
  });

  it("Delete button is disabled when input is empty", () => {
    setup();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("Delete button is disabled with wrong text", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Elfrfdet" } });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("Delete button is enabled with exact match", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeEnabled();
  });

  it("calls onConfirm when Delete clicked with match", async () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("shows pending state on Delete button", () => {
    setup({ pending: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    expect(screen.getByRole("button", { name: /deleting/i })).toBeDisabled();
  });

  it("shows error banner when errorBanner provided", () => {
    setup({
      errorBanner: {
        code: "version_has_in_flight_jobs",
        message: "Cancel running jobs that use this version before deleting it.",
      },
    });
    expect(screen.getByText(/Cancel running jobs/)).toBeInTheDocument();
  });

  it("does not close dialog on error", () => {
    const { onOpenChange } = setup({
      errorBanner: { code: "X", message: "Y" },
    });
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd frontend && pnpm vitest run tests/unit/DeleteConfirmDialog.test.tsx
```

Expected: FAIL — `DeleteConfirmDialog` doesn't exist.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/common/DeleteConfirmDialog.tsx`:

```tsx
import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface ErrorBanner {
  code?: string;
  message?: string;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmText: string;
  onConfirm: () => void | Promise<void>;
  pending: boolean;
  errorBanner: ErrorBanner | null;
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText,
  onConfirm,
  pending,
  errorBanner,
}: Props) {
  const [typed, setTyped] = useState("");
  const matches = typed === confirmText;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!pending) onOpenChange(o);
        if (!o) setTyped("");
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-destructive">{title}</DialogTitle>
          <DialogDescription asChild>
            <div className="text-sm text-muted-foreground">{description}</div>
          </DialogDescription>
        </DialogHeader>

        {errorBanner ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {errorBanner.message ?? errorBanner.code ?? "Delete failed."}
          </div>
        ) : null}

        <div className="space-y-2 py-2">
          <Label htmlFor="delete-confirm-input">
            Type <span className="font-mono font-semibold">{confirmText}</span> to confirm
          </Label>
          <Input
            id="delete-confirm-input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmText}
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || pending}
            onClick={() => onConfirm()}
          >
            {pending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

If `<Input>` or `<Label>` don't exist in `frontend/src/components/ui/`, they're shadcn primitives — install via `pnpm dlx shadcn-ui@latest add input label`.

- [ ] **Step 4: Run tests to verify they pass**

```
cd frontend && pnpm vitest run tests/unit/DeleteConfirmDialog.test.tsx
```

Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/common/DeleteConfirmDialog.tsx frontend/tests/unit/DeleteConfirmDialog.test.tsx
git commit -m "$(cat <<'EOF'
feat(common): DeleteConfirmDialog with typing-name confirmation (phase 13a A4)

Reusable destructive-action dialog matching the GitHub repo-delete /
Vercel project-delete pattern. Disabled until typed text exactly matches
confirmText. Shows pending state during the mutation. Error banner stays
inline on 409 (does not auto-close), so the user can read why the delete
failed and act on it.
EOF
)"
```

---

## Task 4.5: Frontend — `useDeleteVersion` hook + tighten `useDeleteDetector` invalidation

**Files:**
- Modify: `frontend/src/api/queries/detectors.ts`

- [ ] **Step 1: Regenerate schema for the new endpoint**

```bash
cd frontend && pnpm gen:schema
```

Expected: `schema.gen.ts` now contains `DELETE /api/v1/detectors/{detector_id}/versions/{tag}`.

- [ ] **Step 2: Add the hook**

In `frontend/src/api/queries/detectors.ts`, replace the existing `useDeleteDetector` and add `useDeleteVersion`:

```tsx
export function useDeleteDetector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await client.DELETE("/api/v1/detectors/{detector_id}", {
        params: { path: { detector_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: (_data, id) => {
      // Phase 13a A4: invalidate list and the deleted detector's detail
      qc.invalidateQueries({ queryKey: detectorsKeys.list() });
      qc.invalidateQueries({ queryKey: detectorsKeys.detail(id) });
    },
  });
}

export function useDeleteVersion(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (tag: string) => {
      const { error } = await client.DELETE(
        "/api/v1/detectors/{detector_id}/versions/{tag}",
        { params: { path: { detector_id: detectorId, tag } } },
      );
      if (error) throw error;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: detectorsKeys.versions(detectorId) });
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) });
    },
  });
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/queries/detectors.ts frontend/src/api/schema.gen.ts
git commit -m "$(cat <<'EOF'
feat(detectors): useDeleteVersion hook + invalidation cleanup (phase 13a A4)

Adds the matching frontend mutation for the new
DELETE /detectors/{id}/versions/{tag} backend endpoint.
useDeleteDetector now invalidates both list and detail queries on
success.
EOF
)"
```

---

## Task 4.6: Frontend — wire delete buttons in detector list page

**Files:**
- Modify: `frontend/src/routes/_authed.detectors._index.tsx`
- Verify: `frontend/src/components/ui/dropdown-menu.tsx` exists (shadcn)

- [ ] **Step 1: Verify dropdown-menu component exists**

```bash
ls frontend/src/components/ui/dropdown-menu.tsx
```

If missing, install:

```bash
cd frontend && pnpm dlx shadcn-ui@latest add dropdown-menu
```

- [ ] **Step 2: Read the existing detector list page to understand structure**

```bash
cat frontend/src/routes/_authed.detectors._index.tsx
```

Identify the table row rendering. Add an actions column (or extend an existing one) with a `MoreHorizontal` icon button that opens a dropdown menu.

- [ ] **Step 3: Add the row dropdown menu and delete handler**

Modify `frontend/src/routes/_authed.detectors._index.tsx` (the columns array) to add:

```tsx
import { useState } from "react";
import { MoreHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useDeleteDetector } from "@/api/queries/detectors";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";

// Inside the component:
function DetectorRowActions({ detector }: { detector: { id: string; name: string } }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<{ code?: string; message?: string } | null>(null);
  const deleteMut = useDeleteDetector();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm">
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={(e) => {
              e.preventDefault();
              setOpen(true);
            }}
          >
            Delete detector
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => { setOpen(o); if (!o) setError(null); }}
        title={`Delete detector ${detector.name}?`}
        description={
          <>
            This soft-deletes the detector. All versions and Harbor images
            will be permanently purged. Historical jobs and runs remain
            visible but will reference a deleted detector.
          </>
        }
        confirmText={detector.name}
        onConfirm={async () => {
          try {
            await deleteMut.mutateAsync(detector.id);
            setOpen(false);
          } catch (e) {
            const detail = (e as { detail?: { code?: string; message?: string } })?.detail;
            setError(detail ?? { message: "Delete failed." });
          }
        }}
        pending={deleteMut.isPending}
        errorBanner={error}
      />
    </>
  );
}
```

Add the actions column to the table columns:

```tsx
{
  id: "actions",
  header: "",
  cell: ({ row }) => <DetectorRowActions detector={row.original} />,
}
```

Match the exact import paths and patterns in the existing file. The exact column array might be inline; place the new `actions` column at the end.

- [ ] **Step 4: Manual smoke check via dev server**

```bash
cd frontend && pnpm dev
```

Visit `http://localhost:5173/detectors`, verify each row has a `…` menu, click it, verify dropdown shows "Delete detector" in red, click it, verify dialog opens.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.detectors._index.tsx frontend/src/components/ui/dropdown-menu.tsx
git commit -m "$(cat <<'EOF'
feat(detectors): row dropdown menu with Delete action on /detectors (phase 13a A4)
EOF
)"
```

---

## Task 4.7: Frontend — wire delete buttons in detector detail page

**Files:**
- Modify: `frontend/src/routes/_authed.detectors.$id.tsx`

- [ ] **Step 1: Add a "Delete" button in the page header**

Modify the header `<div>` (around `_authed.detectors.$id.tsx:94-98`) to include a Delete button:

```tsx
<div className="flex items-center justify-between">
  <h1 className="text-2xl font-semibold">{det.display_name}</h1>
  <div className="flex items-center gap-2">
    <DetectorDeleteButton detector={det} />
    <Link to="/detectors" className="text-sm text-muted-foreground">← back</Link>
  </div>
</div>
```

Implement `DetectorDeleteButton` near the bottom of the same file (or in a separate co-located component file if you prefer):

```tsx
function DetectorDeleteButton({ detector }: { detector: { id: string; name: string } }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<{ code?: string; message?: string } | null>(null);
  const deleteMut = useDeleteDetector();
  const nav = useNavigate();

  return (
    <>
      <Button
        variant="destructive"
        size="sm"
        onClick={() => setOpen(true)}
      >
        Delete
      </Button>
      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => { setOpen(o); if (!o) setError(null); }}
        title={`Delete detector ${detector.name}?`}
        description={
          <>
            This soft-deletes the detector. All versions and Harbor
            images will be permanently purged. Historical jobs and runs
            remain visible but will reference a deleted detector.
          </>
        }
        confirmText={detector.name}
        onConfirm={async () => {
          try {
            await deleteMut.mutateAsync(detector.id);
            nav("/detectors");
          } catch (e) {
            const detail = (e as { detail?: { code?: string; message?: string } })?.detail;
            setError(detail ?? { message: "Delete failed." });
          }
        }}
        pending={deleteMut.isPending}
        errorBanner={error}
      />
    </>
  );
}
```

Add the `useNavigate` import:

```tsx
import { useParams, Link, useNavigate } from "react-router";
```

- [ ] **Step 2: Add per-version Delete buttons**

Modify the `versionsCols` columns array (`_authed.detectors.$id.tsx:48-63`). The existing `actions` cell currently has only `View manifest`; extend it:

```tsx
{
  id: "actions",
  header: "",
  cell: ({ row }) => (
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="sm" onClick={() => setOpenManifestTag(row.original.tag)}>
        View manifest
      </Button>
      <VersionDeleteButton detectorId={id} version={row.original} />
    </div>
  ),
},
```

Implement `VersionDeleteButton` near the bottom of the file:

```tsx
function VersionDeleteButton({
  detectorId,
  version,
}: {
  detectorId: string;
  version: { tag: string };
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<{ code?: string; message?: string } | null>(null);
  const deleteMut = useDeleteVersion(detectorId);

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
        onClick={() => setOpen(true)}
      >
        Delete
      </Button>
      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => { setOpen(o); if (!o) setError(null); }}
        title={`Delete version ${version.tag}?`}
        description={
          <>
            This soft-deletes only this version. The Harbor image for
            this tag will be permanently purged. Historical jobs that
            ran against this version remain visible.
          </>
        }
        confirmText={version.tag}
        onConfirm={async () => {
          try {
            await deleteMut.mutateAsync(version.tag);
            setOpen(false);
          } catch (e) {
            const detail = (e as { detail?: { code?: string; message?: string } })?.detail;
            setError(detail ?? { message: "Delete failed." });
          }
        }}
        pending={deleteMut.isPending}
        errorBanner={error}
      />
    </>
  );
}
```

Add imports at the top of the file:

```tsx
import { useDeleteDetector, useDeleteVersion } from "@/api/queries/detectors";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";
```

- [ ] **Step 3: Run TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual smoke check**

```bash
cd frontend && pnpm dev
```

Visit a detector detail page; verify the Delete button appears top-right; click it; verify dialog opens; type the wrong name; verify Delete button stays disabled; type the correct name; verify Delete enables. Repeat for a version row's Delete button.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.detectors.$id.tsx
git commit -m "$(cat <<'EOF'
feat(detectors): Delete buttons on detail page header + per-version row (phase 13a A4)
EOF
)"
```

---

## Task 4.8: Frontend — playwright e2e tests for delete

**Files:**
- Modify: `frontend/tests/e2e/detectors.spec.ts`

- [ ] **Step 1: Write the e2e tests**

Add to `frontend/tests/e2e/detectors.spec.ts`:

```ts
test.describe("Delete detector / version", () => {
  test("delete detector happy path", async ({ page, seedDetector }) => {
    const { id, name } = await seedDetector({ name: "to-delete" });
    await page.goto(`/detectors/${id}`);
    await page.getByRole("button", { name: /^Delete$/ }).first().click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const deleteBtn = dialog.getByRole("button", { name: /^Delete$/ });
    await expect(deleteBtn).toBeDisabled();

    await dialog.getByRole("textbox").fill("wrong-name");
    await expect(deleteBtn).toBeDisabled();

    await dialog.getByRole("textbox").fill(name);
    await expect(deleteBtn).toBeEnabled();
    await deleteBtn.click();

    await expect(page).toHaveURL("/detectors");
    await expect(page.getByText(name)).not.toBeVisible();
  });

  test("delete version happy path", async ({ page, seedDetectorWithVersion }) => {
    const { detectorId, tag } = await seedDetectorWithVersion({
      name: "rfdet", tag: "v1.0.0",
    });
    await page.goto(`/detectors/${detectorId}`);
    await page.getByRole("tab", { name: /versions/i }).click();
    await page.getByRole("button", { name: /^Delete$/ }).click();

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill(tag);
    await dialog.getByRole("button", { name: /^Delete$/ }).click();

    // Version disappears from list
    await expect(page.getByRole("cell", { name: tag })).not.toBeVisible();
  });

  test("delete blocked by in-flight job", async ({
    page, seedDetectorWithRunningJob,
  }) => {
    const { detectorId, name } = await seedDetectorWithRunningJob({ name: "blocked" });
    await page.goto(`/detectors/${detectorId}`);
    await page.getByRole("button", { name: /^Delete$/ }).first().click();

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill(name);
    await dialog.getByRole("button", { name: /^Delete$/ }).click();

    // Dialog stays open with error banner
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/cancel running jobs/i)).toBeVisible();
    await expect(page).toHaveURL(`/detectors/${detectorId}`);   // didn't navigate
  });
});
```

`seedDetector`, `seedDetectorWithVersion`, `seedDetectorWithRunningJob` are fixtures — extend the existing fixtures file.

- [ ] **Step 2: Run tests**

```
cd frontend && pnpm playwright test detectors.spec.ts -g "Delete"
```

Expected: all PASS (after fixtures wired).

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/e2e/detectors.spec.ts
git commit -m "test(e2e): delete detector / version + in-flight 409 (phase 13a A4)"
```

---

## Task 5.1: A5 evaluate metrics — reproduction & diagnosis

**Files:**
- (no code changes in this task; produces a written diagnosis)
- Create: `docs/superpowers/notes/2026-04-28-phase13a-a5-evaluate-metrics-investigation.md`

- [ ] **Step 1: Query DB for sample evaluate jobs**

On the deployed cluster (or a port-forwarded staging DB), run:

```sql
SELECT id, summary_metrics, status, started_at, finished_at,
       (finished_at - started_at) AS duration
FROM job
WHERE type = 'evaluate' AND status = 'succeeded'
ORDER BY finished_at DESC LIMIT 5;
```

Record the result — pay attention to which (if any) have populated `summary_metrics.metrics`.

- [ ] **Step 2: For one symptomatic job, check the events table**

Pick a job whose `summary_metrics` is empty / null. Then:

```sql
SELECT kind, payload, ts
FROM job_event
WHERE job_id = '<the-job-id>'
ORDER BY ts;
```

Categorize the result into one of:
- **0 rows** — events never reached the backend.
- **N rows but no `metric` / `confusion_matrix` kind** — maldet didn't emit them.
- **N rows including `metric` and `confusion_matrix`** — projection ran but didn't pick them up (unlikely given current code; check for projection exception).

- [ ] **Step 3: If branch (a) (0 rows), inspect the event-tailer**

```bash
kubectl -n lolday-jobs get pods -l "lolday.job-id=<the-job-id>"
# (Pod may have been GC'd — Volcano TTL)
kubectl logs <pod-name> -c event-tailer --previous   # if pod exists
```

Look for:
- "wrote 0 events to backend" → tailer exited before reading the jsonl.
- HTTP errors on POST.
- File-path errors (looking for `/mnt/output/events.jsonl` but it's elsewhere).

If the pod is gone, reproduce with a small evaluate job and `kubectl describe pod` quickly while it's running, then `kubectl logs` after termination.

- [ ] **Step 4: If branch (b) (events present, no metric kind), inspect maldet**

Open `/home/bolin8017/Documents/repositories/maldet/src/maldet/runner.py` lines 131-155 (the `if stage == "evaluate":` branch).

Cross-check:
- `evaluator.evaluate(...)` returns a `MetricReport` and emits `log_metric` per metric inside `BinaryClassification.evaluate`.
- The `output_dir` and `events.jsonl` writer path matches the `event-tailer` reader path.

If a difference between train and evaluate is found (path, log call, etc.), the maldet PR is needed.

- [ ] **Step 5: If branch (c) (events present but projection failed), search reconciler logs**

```bash
kubectl logs -n lolday deploy/backend | grep -i "summary_metrics projection failed"
```

Capture the stack trace if any.

- [ ] **Step 6: Document the findings**

Create `docs/superpowers/notes/2026-04-28-phase13a-a5-evaluate-metrics-investigation.md`:

```markdown
# Phase 13a A5 — Evaluate `summary_metrics` Investigation

**Date:** 2026-04-28
**Investigator:** <name>

## Sampled jobs

| Job ID | summary_metrics | duration | notes |
|---|---|---|---|

## Events table inspection

(paste output)

## Diagnosis

(branch a / b / c / mixed)

## Root cause

(short paragraph)

## Recommended fix

(maldet PR / event-tailer flush / reconciler / something else)
```

- [ ] **Step 7: Commit the investigation note**

```bash
git add docs/superpowers/notes/2026-04-28-phase13a-a5-evaluate-metrics-investigation.md
git commit -m "docs(phase13a-a5): investigation findings for evaluate summary_metrics emptiness"
```

---

## Task 5.2: A5 fix (branch determined by 5.1)

**Files:** depend on root cause:
- If event-tailer flush bug: `charts/lolday/helpers/event-tailer/*` (Python)
- If reconciler projection bug: `backend/app/reconciler.py`
- If maldet emit bug: external maldet PR; Phase 13a includes only a tracking note

- [ ] **Step 1: Pick the branch from 5.1**

Read the investigation note. Identify branch and target component.

- [ ] **Step 2: Write a regression test (one of)**

If event-tailer flush:

```python
# charts/lolday/helpers/event-tailer/test_tailer.py (new)
def test_tailer_drains_after_main_container_exits():
    """Sidecar must read remaining events.jsonl bytes after main container
    sends SIGTERM but before sidecar itself exits."""
    # Set up tmpfs file with 5 events written, then immediate SIGTERM
    # Run tailer subprocess, assert it POSTed all 5 before exiting.
```

If reconciler projection:

```python
# backend/tests/test_reconciler_summary_projection.py (extend)
async def test_project_summary_metrics_for_evaluate_with_metric_events(...):
    """Seed evaluate job + metric events, run projection, assert
    summary_metrics.metrics contains all event values."""
```

If maldet emit: skip — task becomes "open maldet PR" and lolday only adds a tracking note.

- [ ] **Step 3: Run the test (red)**

```
# event-tailer
cd charts/lolday/helpers/event-tailer && python -m pytest test_tailer.py -xvs

# reconciler
cd backend && uv run pytest tests/test_reconciler_summary_projection.py -xvs
```

Expected: FAIL.

- [ ] **Step 4: Apply the targeted fix**

Pattern depends on branch. Concrete examples:

- **Event-tailer flush**: trap SIGTERM in the sidecar, read the remaining file, POST, then exit. Industry-standard pattern (Fluent Bit `Storage.Buffer.Drain`, Vector `shutdown_drain_secs`). Add a `flushOnTerm` configurable behavior; ensure pod's `terminationGracePeriodSeconds` is at least 5s in `app/services/job_spec.py`.

- **Reconciler projection**: read the actual exception from logs and patch `_project_summary_metrics`.

- **maldet emit**: open a PR in `/home/bolin8017/Documents/repositories/maldet`. Lolday Phase 13a only adds:

```markdown
# docs/superpowers/notes/2026-04-28-phase13a-a5-maldet-pr-tracker.md
# Tracker: maldet PR for evaluate stage_end / metric emit
- PR: <url when filed>
- Status: <draft / under review / merged>
- Affected lolday version: must include reconciler bump after maldet release
```

- [ ] **Step 5: Run the test (green)**

```
cd backend && uv run pytest <test path>
```

Expected: PASS.

- [ ] **Step 6: Manual verification**

Submit an evaluate job in staging; verify `summary_metrics.metrics` populates within 30s of completion.

- [ ] **Step 7: Commit**

```bash
git add <touched paths>
git commit -m "$(cat <<'EOF'
fix(phase13a-a5): <one-line root cause and fix>

Investigation in docs/superpowers/notes/2026-04-28-phase13a-a5-evaluate-metrics-investigation.md.
Regression test ensures evaluate jobs with metric events end up with
populated summary_metrics.metrics.
EOF
)"
```

---

## Task 6.1: Build + push backend phase13a image

**Files:**
- Modify: `charts/lolday/values.yaml` (`backend.image.tag`)

- [ ] **Step 1: Tag and push backend image**

```bash
cd backend
docker build -t harbor.lolday.svc:80/lolday/backend:phase13a .
docker push harbor.lolday.svc:80/lolday/backend:phase13a
```

(or use the existing build-helper pattern; see prior phase plans for the exact pipeline if different.)

- [ ] **Step 2: Bump tag in chart**

Edit `charts/lolday/values.yaml`:

```yaml
backend:
  image:
    tag: phase13a
```

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "chore(deploy): bump backend default tag to phase13a"
```

---

## Task 6.2: Build + push frontend phase13a image

**Files:**
- Modify: `charts/lolday/values.yaml` (`frontend.image.tag`)

- [ ] **Step 1: Tag and push frontend image**

```bash
cd frontend
docker build -t harbor.lolday.svc:80/lolday/frontend:phase13a .
docker push harbor.lolday.svc:80/lolday/frontend:phase13a
```

- [ ] **Step 2: Bump tag in chart**

```yaml
frontend:
  image:
    tag: phase13a
```

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "chore(deploy): bump frontend default tag to phase13a"
```

---

## Task 6.3: Deploy and run manual verification

**Files:** none (deployment + smoke check).

- [ ] **Step 1: Run alembic migration**

```bash
kubectl -n lolday exec deploy/backend -- alembic upgrade head
```

Expected: success; `detectorversionstatus` enum now includes `deleted`.

- [ ] **Step 2: helm upgrade**

```bash
helm upgrade --install lolday charts/lolday -n lolday --values charts/lolday/values.yaml
```

Wait for rollout:

```bash
kubectl -n lolday rollout status deploy/backend deploy/frontend
```

- [ ] **Step 3: Manual verification (mirrors spec §Testing strategy → Manual verification)**

1. Visit `/detectors/<id>` Versions tab; click `View manifest` on a phase 11e+ version → manifest tree visible.
2. Click `View manifest` on a legacy version (if any in DB) → fallback message visible.
3. Click `Logs` on a recent build → log content visible (not `(no output)`).
4. Cancel a build that's in `clone` stage; click `Logs` → clone container's git error visible.
5. Visit `/jobs` with > 50 jobs → logout still in viewport at bottom-left; sidebar doesn't scroll.
6. Visit `/runs` with many runs → same logout visibility check.
7. Visit a detector detail page; click Delete; confirm; verify detector gone from list.
8. Visit a detector detail page → Versions tab; click Delete on a version; confirm; version gone from list.
9. Submit a job; while it's queued, try deleting its version → 409 banner inline with "Cancel running jobs" message + link.

- [ ] **Step 4: Commit any documented fixes if smoke uncovers issues**

If smoke uncovers any new issues, file as bugs and either fix-and-recommit or defer to Phase 13a.1.

---

## Self-Review

### Spec coverage check

| Spec section | Plan task |
|---|---|
| §1 A1 schema nullable | 1.1 |
| §1 A1 frontend fallback + click verification | 1.2 |
| §2 A2 generic helper | 2.1 |
| §2 A2 wire wrappers | 2.2 |
| §3 A3 layout | 3.1 |
| §4.1 enum + migration | 4.1 |
| §4.2a new endpoint | 4.2 |
| §4.2b strengthen detector delete | 4.3 |
| §4.4 DeleteConfirmDialog | 4.4 |
| §4.5 hooks | 4.5 |
| §4.3 wire detector list | 4.6 |
| §4.3 wire detector detail | 4.7 |
| §4.6 e2e | 4.8 |
| §5 A5 investigation | 5.1 |
| §5 A5 fix | 5.2 |
| Migration & deploy | 6.1 / 6.2 / 6.3 |

All sections accounted for.

### Placeholder scan

- Task 5.2 deliberately branches by Task 5.1 outcome; concrete code is provided per branch but the actual chosen branch is determined by the investigation. This is correct (the branch is unknowable without investigation), not a missed placeholder.
- Two `<previous-revision-id>` and `<hash>` placeholders are explicit Alembic-fill instructions; the engineer fills them when running `alembic revision`.
- "(or use the existing build-helper pattern...)" in 6.1 is a callback to the lab's existing pattern. Engineers reading this codebase have prior phase plans as references; if it's actually missing, they'll add it.

### Type consistency

- `NON_TERMINAL_JOB_STATUSES` defined consistently in 4.2 and reused in 4.3.
- `DetectorVersionStatus.DELETED` added in 4.1, used in 4.2.
- `DeleteConfirmDialog` props match between 4.4 (component) and 4.6/4.7 (callers).
- `useDeleteVersion(detectorId)` (parameterized) in 4.5 matches caller pattern in 4.7.

No inconsistencies found.
