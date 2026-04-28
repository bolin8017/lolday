# Phase 13a: Bug Fixes, Delete, and Layout — Design Specification

## Overview

Phase 12 left several user-visible defects that block daily research workflow:

1. **`View manifest` does nothing visible.** Clicking the button on `/detectors/:id` Versions tab leaves the page unchanged. Root cause: the `VersionDetailRead.manifest` Pydantic field is non-nullable (`dict[str, Any]`), but versions built before phase 11e have `manifest IS NULL` in the database. The serializer raises 500, the frontend's `error` branch silently shows "Failed to load manifest" inside the Sheet — but Sheet open/close events also need verification (the user reports no visible Sheet at all).
2. **Build logs are always empty.** Every `Logs` button in the Builds tab opens a Sheet showing `(no output)`. Root cause: `app/reconciler.py:_capture_log_tail` reads container `"kaniko"`, but the build pod actually runs rootless BuildKit with container name `"buildkit"` (`app/services/build.py:261`). The K8s API raises a 404, the bare-`except ApiException` swallows the error, and `log_tail` stays `""`. The same module also fails to surface init-container logs (`clone`, `validate`) when the build dies before BuildKit starts.
3. **No way to delete a detector or version from the UI.** The backend `DELETE /api/v1/detectors/{id}` endpoint and the `useDeleteDetector` hook both exist but have never been wired to a button. There is no per-version delete endpoint at all.
4. **Sidebar account/Logout disappears on `/jobs` and `/runs`.** Layout bug: `_authed.tsx` uses `min-h-screen` on the parent flex container, so when `<main>` content exceeds viewport height the parent grows beyond the viewport, the `<aside>` flex item grows with it, and the bottom-anchored profile/logout block drops below the fold.
5. **Evaluate jobs report empty `summary_metrics`.** User-reported observation: `/jobs/:id` Summary tab shows "No metrics recorded yet" for evaluate jobs. The reconciler code path for evaluate looks correct on inspection (`_handle_job_succeeded` is not type-gated), so this is treated as an investigation task — reproduce, locate the breakage in the events pipeline (maldet emit → event-tailer → JobEvent → projection), then fix at root cause rather than band-aiding the display.

Phase 13a fixes all five at root cause and adds the missing delete UX. No display redesign in this phase — that is Phase 13b.

**Authorization:** Breaking schema changes (new `DetectorVersionStatus.DELETED` enum value, nullable `VersionDetailRead.manifest`) are explicitly approved.

---

## Scope

### In scope

1. **A1. View manifest fix** — `VersionDetailRead.manifest` becomes `dict | None`; frontend handles null with a clear fallback message; click flow verified end-to-end.
2. **A2. Build / job log capture fix** — replace hard-coded container names with a generic helper that picks the failing container (init or main) based on `_extract_failure_reason`; merge `_capture_log_tail` and `_capture_job_log_tail` into one parameterized helper.
3. **A3. Sidebar layout fix** — convert `_authed.tsx` to a fixed-viewport app shell (`h-screen overflow-hidden` parent, scrollable main); profile/logout always anchored.
4. **A4. Delete detector + delete version** — UI buttons + new backend `DELETE /detectors/{id}/versions/{tag}` endpoint; soft-delete pattern with Harbor purge; `DetectorVersionStatus.DELETED` enum addition; in-flight job protection (409); GitHub-style typing-name confirmation dialog.
5. **A5. Evaluate `summary_metrics` investigation + targeted fix** — reproduce the empty-metrics symptom on a real evaluate job, follow the events pipeline, fix at the actual breakage (maldet emit, event-tailer flush, or reconciler projection).

### Out of scope (deferred to Phase 13b)

- Per-type Job Detail layout
- `MetricCards` whitelist removal / per-class metrics
- Resolved config tree-view rewrite
- Hyperparameters submit form polish
- Runs page redesign
- MLflow UI exposure

---

## Architecture

### Layered impact

| Layer | A1 manifest | A2 logs | A3 sidebar | A4 delete | A5 evaluate metrics |
|---|---|---|---|---|---|
| Backend schema/model | ✓ (`VersionDetailRead.manifest` nullable) | — | — | ✓ (`DetectorVersionStatus.DELETED` enum) | possibly |
| Backend endpoint | — | — | — | ✓ (new `DELETE .../versions/{tag}`) | possibly |
| Backend reconciler | — | ✓ (rewrite log capture) | — | — | possibly |
| DB migration | — | — | — | ✓ (enum value add) | — |
| Frontend route | ✓ (`detectors.$id.tsx`) | — | ✓ (`_authed.tsx`) | ✓ (multiple routes) | — |
| Frontend component | ✓ (manifest fallback) | — | ✓ (`Sidebar.tsx` no change needed) | ✓ (`<DeleteConfirmDialog>` new) | — |
| External (maldet) | — | — | — | — | possibly (stage_end emit on evaluate) |

### Why a single phase

The five items are intentionally bundled because each is small (≤ 1 day) and all surface in the same daily user flow (Detectors → click around → Jobs → click around). Bundling them reduces test/deploy overhead vs. five separate phases. Phase 13b's UX redesign is split out only because it's substantially larger.

---

## Section 1 — A1. View manifest fix

### 1.1 Root cause

`backend/app/schemas/detector.py`:

```python
class VersionDetailRead(VersionRead):
    manifest: dict[str, Any]   # phase 11e — full maldet 1.1 manifest (JSONB column)
```

`manifest` is **required**. For any `DetectorVersion` row built before phase 11e (or built but with a maldet < 1.1 detector that did not emit `MALDET_MANIFEST_B64`), the database column is NULL. Pydantic raises `ValidationError`, FastAPI returns 500, the frontend's `useDetectorVersion` query goes to its `error` state, `<ManifestView>` renders "Failed to load manifest." The Sheet itself does open — what user sees is the Sheet with an error message.

Secondary verification needed: confirm the Sheet actually opens (the user's "nothing happens" wording is ambiguous between "Sheet opens with error" and "click does nothing"). If the Sheet does not open, debug whether `<DataTable>`'s `onRowClick` is intercepting the button's click event.

### 1.2 Fix

**Backend** (`backend/app/schemas/detector.py`):

```python
class VersionDetailRead(VersionRead):
    manifest: dict[str, Any] | None   # null for legacy versions built before maldet 1.1
```

**Frontend** (`frontend/src/routes/_authed.detectors.$id.tsx`, `<ManifestView>`):

```tsx
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
```

**Click flow verification**: add a smoke playwright test that asserts the Sheet becomes visible after click. If it doesn't, inspect `<DataTable onRowClick>` propagation; current `_authed.detectors.$id.tsx` doesn't set `onRowClick` on the versions table, so the propagation should be clean — but the test guards against future regressions.

### 1.3 Tests

- `test_get_version_legacy_null_manifest_returns_200` (pytest, `backend/tests/test_routers_detectors.py`): create a `DetectorVersion` row with `manifest=None`, GET the endpoint, assert 200 and `{"manifest": null, ...}`.
- `test_view_manifest_sheet_opens` (playwright, `frontend/tests/e2e/detectors.spec.ts`): seed a detector with one version, click `View manifest`, assert Sheet visible and either tree view or fallback message visible.

### 1.4 Files touched

- `backend/app/schemas/detector.py`
- `frontend/src/routes/_authed.detectors.$id.tsx` (`<ManifestView>` component)
- `backend/tests/test_routers_detectors.py`
- `frontend/tests/e2e/detectors.spec.ts`

---

## Section 2 — A2. Build / job log capture fix

### 2.1 Root cause

Two near-identical functions in `backend/app/reconciler.py`:

```python
async def _capture_log_tail(b: DetectorBuild) -> str:        # line 603
    ...
    log = core_v1().read_namespaced_pod_log(
        ..., container="kaniko", tail_lines=200,             # ← wrong name
    )
    return log[-settings.BUILD_LOG_TAIL_BYTES:]
```

```python
async def _capture_job_log_tail(j: Job) -> str:              # line 1071
    ...
    log = core_v1().read_namespaced_pod_log(
        ..., container="detector", tail_lines=200,           # ← right name, but no init fallback
    )
    return log[-8192:]
```

Issues:
- Build pod's main container is `"buildkit"`, not `"kaniko"`. K8s API returns 404, bare except swallows the error, log is empty.
- Both functions only read the main container. When a build/job dies in an init container (`clone`/`validate` for builds, `config-writer`/`model-fetcher` for jobs), the main container never starts and main-only log capture returns empty.

### 2.2 Fix

Refactor into one generic helper:

```python
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
    """Capture log tail from the failing or main container of a pod.

    Order:
      1. If failure_reason names a known container (e.g. "clone_failed: ..."),
         try that container first.
      2. Otherwise try main_container.
      3. On any single read failure, fall back to attempting each init
         container in order, then the main container, concatenating whatever
         we get with a header marking each container.
      4. If everything fails, return "" (logged as backend error).
    """
```

Both `_capture_log_tail(b)` and `_capture_job_log_tail(j)` become thin wrappers:

```python
async def _capture_log_tail(b: DetectorBuild) -> str:
    return await _capture_pod_logs(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
        main_container="buildkit",
        init_containers=("clone", "validate"),
        failure_reason=b.failure_reason,
        tail_bytes=settings.BUILD_LOG_TAIL_BYTES,
    )

async def _capture_job_log_tail(j: Job) -> str:
    return await _capture_pod_logs(
        namespace=settings.JOB_NAMESPACE,
        label_selector=f"lolday.job-id={j.id}",
        main_container="detector",
        init_containers=("config-writer", "model-fetcher"),
        failure_reason=j.failure_reason,
        tail_bytes=8192,
    )
```

Failure-reason → container mapping uses the strings already produced by `_extract_failure_reason` ("clone_failed: exit=1" → `clone`).

### 2.3 Tests

`backend/tests/test_reconciler_log_capture.py` (new file):

- `test_capture_pod_logs_main_container_success` — buildkit returns log, used as-is.
- `test_capture_pod_logs_falls_back_to_init_when_main_missing` — main container 404, clone has logs, returns clone log with header.
- `test_capture_pod_logs_uses_failure_reason_hint` — `failure_reason="validate_failed: exit=2"`, asserts `validate` queried first.
- `test_capture_pod_logs_returns_empty_when_all_fail` — all containers 404, returns "".
- `test_capture_job_log_tail_picks_up_model_fetcher_failure` — evaluate job, `failure_reason="model-fetcher_failed: ..."`.

Use `unittest.mock.patch` on `core_v1().read_namespaced_pod_log` and `list_namespaced_pod`.

### 2.4 Files touched

- `backend/app/reconciler.py` (refactor `_capture_log_tail`, `_capture_job_log_tail`; add `_capture_pod_logs` helper)
- `backend/tests/test_reconciler_log_capture.py` (new)

---

## Section 3 — A3. Sidebar layout fix

### 3.1 Root cause

`frontend/src/routes/_authed.tsx`:

```tsx
<div className="flex min-h-screen">
  <Sidebar />
  <div className="flex flex-1 flex-col">
    <TopBar />
    <main className="flex-1 overflow-auto bg-background p-6">
      <Outlet />
    </main>
  </div>
</div>
```

`min-h-screen` lets the parent grow beyond viewport when `<main>` content is taller. The `<aside>` is a flex item with a `flex-1` nav and a fixed-height bottom block; when the parent grows, the bottom block stays at the bottom of the (taller) parent — i.e., below the visible viewport.

### 3.2 Fix — fixed-viewport app shell

```tsx
<div className="flex h-screen overflow-hidden">
  <Sidebar />
  <div className="flex flex-1 flex-col overflow-hidden">
    <TopBar />
    <main className="flex-1 overflow-y-auto bg-background p-6">
      <Outlet />
    </main>
  </div>
</div>
```

Changes:
- Parent: `min-h-screen` → `h-screen overflow-hidden`.
- Middle column: add `overflow-hidden`.
- Main: `overflow-auto` → `overflow-y-auto` (explicit Y-only).

`Sidebar.tsx` itself unchanged: receives `h-screen` from the parent, internal flex layout pins the bottom block to the visible viewport bottom.

This is the standard SaaS app shell pattern (Vercel Dashboard, GitHub repo page, Linear, Slack web). Rejected `position: sticky` because flex-item sticky has inconsistent browser behavior and the body would still scroll, putting the scrollbar in the wrong place visually.

### 3.3 Tests

`frontend/tests/e2e/layout.spec.ts` (new):

- `test_logout_visible_on_jobs_with_long_list` — seed many jobs, visit `/jobs`, assert logout button is in viewport (`page.locator('button:has-text("Logout")').isInViewport()`).
- Same for `/runs`.
- `test_logout_visible_on_short_pages` — visit `/detectors`, same assertion (regression).
- `test_main_scroll_does_not_scroll_body` — assert `document.documentElement.scrollHeight === window.innerHeight` after page load on `/jobs`.

### 3.4 Files touched

- `frontend/src/routes/_authed.tsx`
- `frontend/tests/e2e/layout.spec.ts` (new)

---

## Section 4 — A4. Delete detector + version

### 4.1 Backend — model and migration

**Add `DetectorVersionStatus.DELETED`** to `backend/app/models/detector.py`:

```python
class DetectorVersionStatus(str, Enum):
    ACTIVE = "active"
    RETENTION_PRUNED = "retention_pruned"   # GC by reconciler retention
    DELETED = "deleted"                      # user-initiated delete
```

`list_versions` already filters `WHERE status = ACTIVE`, so DELETED rows are auto-hidden from list views without further code changes.

**Migration** (`backend/migrations/versions/<hash>_phase13a_detector_version_deleted.py`):

```python
def upgrade():
    op.execute("ALTER TYPE detectorversionstatus ADD VALUE IF NOT EXISTS 'deleted'")

def downgrade():
    # PostgreSQL does not support removing enum values without recreating the type.
    # Phase 13a accepts breaking change per spec authorization; downgrade is no-op.
    pass
```

Per Alembic convention, this migration runs in its own revision because `ALTER TYPE ... ADD VALUE` cannot run inside a transaction.

### 4.2 Backend — endpoints

#### 4.2a — New `DELETE /detectors/{detector_id}/versions/{tag}`

In `backend/app/routers/detectors.py`:

```python
NON_TERMINAL_JOB_STATUSES = (
    JobStatus.PENDING, JobStatus.PREPARING, JobStatus.QUEUED, JobStatus.RUNNING,
)

@router.delete("/{detector_id}/versions/{tag}", status_code=204)
async def delete_version(
    tag: str,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
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
            await harbor.delete_artifact("detectors", detector.name, version.image_digest)
        except Exception:
            BACKEND_ERRORS.labels(stage="version_delete_harbor").inc()
            logger.exception(
                "harbor purge on version soft-delete failed",
                extra={"detector_version_id": str(version.id),
                       "detector_name": detector.name, "tag": tag},
            )

    return Response(status_code=204)
```

#### 4.2b — Strengthen existing `DELETE /detectors/{detector_id}`

Add the same in-flight check before soft-deleting:

```python
@router.delete("/{detector_id}", status_code=204)
async def delete_detector(
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
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
    # ... existing soft-delete + Harbor purge logic ...
```

### 4.3 Frontend — entry points

| Location | Action |
|---|---|
| `_authed.detectors._index.tsx` (each row) | Add row dropdown menu with "Delete detector" item |
| `_authed.detectors.$id.tsx` header (right of `← back`) | Red destructive `Delete` button |
| `_authed.detectors.$id.tsx` Versions tab `actions` cell | After `View manifest` button: ghost-red `Delete` button |

### 4.4 Frontend — `<DeleteConfirmDialog>` reusable component

`frontend/src/components/common/DeleteConfirmDialog.tsx` (new):

```tsx
interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmText: string;            // exact string the user must type
  onConfirm: () => Promise<void>;
  pending: boolean;
  errorBanner?: { code?: string; message?: string } | null;
}
```

Behavior (GitHub repo delete pattern):

- Modal Dialog with red header.
- Description explains what's permanently lost (Harbor images) vs. preserved (historical jobs link to deleted detector with badge).
- Input field with placeholder `Type "{confirmText}" to confirm`.
- Delete button stays disabled until input value === confirmText (case-sensitive).
- On 409 from server, show error banner inline (do not close dialog) with the `code`-specific message; if `code == "version_has_in_flight_jobs"` or `"detector_has_in_flight_jobs"`, link to `/jobs?status=running`.

### 4.5 Frontend — hooks

`frontend/src/api/queries/detectors.ts`:

```tsx
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

`useDeleteDetector` already exists; add invalidation of detail and list queries on success.

### 4.6 Tests

#### Backend (pytest, `backend/tests/test_routers_detectors.py`)

- `test_delete_version_soft_deletes` — happy path, status → DELETED, Harbor.delete_artifact called.
- `test_delete_version_blocks_when_in_flight` — seed running job, expect 409 `version_has_in_flight_jobs`.
- `test_delete_version_404_on_unknown_tag`.
- `test_delete_version_409_on_already_deleted` — version.status == DELETED, expect 409 `version_not_active`.
- `test_delete_version_does_not_break_historical_jobs` — delete version, GET `/jobs/{historical_job_id}` still 200 with version reference resolvable.
- `test_delete_detector_blocks_when_in_flight` — strengthen existing endpoint.
- `test_delete_version_owner_only` — non-owner non-admin → 403.

#### Frontend (vitest + playwright)

- vitest unit `<DeleteConfirmDialog>`:
  - Confirm button disabled with empty input.
  - Confirm button disabled with wrong text.
  - Confirm button enabled with exact match.
  - 409 error banner displayed without closing dialog.
- playwright e2e:
  - `delete_detector_happy_path` — open detail, click Delete, type name, confirm, assert detector gone from list.
  - `delete_version_happy_path`.
  - `delete_blocked_by_in_flight_job` — has running job, click Delete, confirm, assert 409 banner with link to jobs filter.

### 4.7 Files touched

- `backend/app/models/detector.py` (enum value)
- `backend/app/routers/detectors.py` (new endpoint, strengthen existing)
- `backend/migrations/versions/<hash>_phase13a_detector_version_deleted.py` (new)
- `backend/tests/test_routers_detectors.py`
- `frontend/src/api/queries/detectors.ts` (new hook + invalidation)
- `frontend/src/components/common/DeleteConfirmDialog.tsx` (new)
- `frontend/src/routes/_authed.detectors._index.tsx` (row menu)
- `frontend/src/routes/_authed.detectors.$id.tsx` (header button + per-version button)
- `frontend/tests/e2e/detectors.spec.ts`

---

## Section 5 — A5. Evaluate `summary_metrics` investigation

### 5.1 Why investigation, not direct fix

User reports `Metrics` card empty for evaluate jobs. Static code analysis suggests the path is correct:

- `BinaryClassification.evaluate` (in maldet) calls `logger.log_metric(k, v)` for accuracy/precision/recall/f1, plus `logger.log_event("confusion_matrix", ...)`.
- `_handle_job_succeeded` calls `_project_summary_metrics` for **all** job types (no `JobType.TRAIN` gate on the projection call).
- `_project_summary_metrics` reads `JobEvent` rows of kind `metric` or `confusion_matrix` and aggregates them into `Job.summary_metrics`.

If any link is silently broken, the symptom is exactly "empty metrics". Direct fix without reproduction risks fixing the wrong link.

### 5.2 Reproduction protocol

Pick the most recent succeeded evaluate job:

```sql
SELECT id, summary_metrics, status, finished_at
FROM job
WHERE type = 'evaluate' AND status = 'succeeded'
ORDER BY finished_at DESC LIMIT 1;
```

Diagnose by following the pipeline backward.

#### Branch 1 — `summary_metrics IS NULL`

Projection never ran. Check:
- `BACKEND_ERRORS{stage="summary_projection"}` Prometheus counter.
- Reconciler logs around the job's `finished_at` for projection exception traces.
- Whether the job actually went through `_handle_job_succeeded` (status transition events, k8s_job_name set).

Likely root cause: projection raised but exception was swallowed; or `_handle_job_succeeded` was bypassed (timeout path, manual cancel).

#### Branch 2 — `summary_metrics = {"metrics": {}, "confusion_matrix": null}`

Projection ran but found no events. Check:

```sql
SELECT kind, payload, ts FROM job_event
WHERE job_id = '<job-id>' ORDER BY ts;
```

- **0 rows**: events never reached the backend. Check event-tailer sidecar logs in the job pod (`kubectl logs <pod> -c event-tailer`). Likely sub-causes:
  - Detector exited before event-tailer flushed buffered jsonl lines.
  - `events.jsonl` not at the path the tailer expects (path mismatch between maldet output and tailer input).
  - HTTP POST to internal endpoint failing (auth, network).
- **Some rows but no `metric`/`confusion_matrix` kinds**: maldet's evaluate runner did not call `logger.log_metric` / `logger.log_event("confusion_matrix")`. Confirm by reading `maldet/src/maldet/evaluators/binary.py` — *as of this spec*, it does emit those, but version drift is possible.

#### Branch 3 — `summary_metrics.metrics` populated but UI shows empty

Pure display problem (whitelist in `MetricCards`). This is **Phase 13b's** territory — `MetricsTable` removal of whitelist will surface the metrics. Phase 13a does not fix this branch.

### 5.3 Likely root cause and fix

Based on code inspection, the highest-probability root cause is **graceful flush in event-tailer**: when the detector container exits quickly (evaluate is much faster than train), the sidecar may be terminated before tailing the final lines of `events.jsonl`.

If reproduction confirms this, fix is in the event-tailer (separate small repo / chart helper):

- Trap container shutdown signal.
- Read remaining bytes of `events.jsonl` after main container exits but before sidecar exits.
- Use Kubernetes pod's `terminationGracePeriodSeconds` to give the sidecar time to flush (current value, if any, must be checked in `services/job_spec.py`).
- Pattern is standard for log-shipper sidecars (Fluent Bit `Storage.Buffer.Drain`, Vector `shutdown_drain_secs`).

Alternatively, if the cause is on the maldet side (e.g., stage_end not emitted on evaluate), this becomes a maldet PR — listed as **external dependency** in the plan.

### 5.4 Tests

Cannot pre-write tests until root cause is known. Plan includes:

- A reproduction step (run an evaluate job, check `summary_metrics`).
- Once root cause known, a regression test specific to that path.

If the fix is in event-tailer flush:
- Integration test: spin up a pod with detector container that exits within 100ms of writing 5 metric events; assert event-tailer captured all 5 and POSTed them.

### 5.5 Files touched

Unknown until investigation completes. Plan will list candidates:

- `charts/lolday/helpers/event-tailer/` (likely)
- `backend/app/reconciler.py` (`_project_summary_metrics`, if a projection bug)
- `maldet` repo (if evaluator missing emit) — out-of-tree, listed as external dep

---

## Migration & Deploy

### Order (low risk → high risk)

1. **Backend migration** — add `DetectorVersionStatus.DELETED` enum value (independent transaction, must run before backend with new model).
2. **Backend deploy** — log capture refactor + new delete endpoint + nullable manifest schema.
3. **Frontend deploy** — new delete UX + sidebar fix + manifest fallback.

A2 (log capture) is purely additive: even if the new helper has a bug, the worst case is `log_tail = ""` — same as today. Safe to deploy together.

A3 (sidebar) is pure CSS in the frontend bundle. Hot-rollback is reverting the bundle.

A4 needs migration before backend deploys (otherwise enum constraint fails on the first DELETE call). Migration is forward-only; the enum can't be removed without recreating the type.

A1 needs the schema change deployed before any clicks happen on legacy versions (otherwise legacy version still 500s). Order doesn't matter relative to other changes.

### Rollback

- A1, A2, A3: revert the deploy.
- A4 backend: revert the deploy. The enum value remains in the DB (harmless — no rows use it after revert because the new endpoint is gone).
- A4 frontend: revert the bundle. Existing `useDeleteDetector` hook still callable but no UI exposes it; safe.

---

## Testing strategy

### Unit (pytest, vitest)

| Area | Coverage |
|---|---|
| `_capture_pod_logs` | All four branches: hint, main success, fallback, all-fail |
| `delete_version` endpoint | 6 cases listed in 4.6 |
| `delete_detector` endpoint | New in-flight check |
| `<DeleteConfirmDialog>` | Disabled / enabled / 409 banner / exact-match logic |
| `<ManifestView>` | null branch and tree-view branch |
| `summary_metrics` projection | Existing tests; A5 may add one regression test |

### Integration (pytest + minikube/kind, optional)

If A5 root cause is event-tailer flush, an integration test with a real job pod is required (mock-only tests miss the timing nature of the bug).

### E2E (playwright)

Smoke tests covering the user-visible flows:

- View manifest opens Sheet (legacy + active versions).
- Delete detector typing-name confirmation.
- Delete version typing-name confirmation.
- Delete blocked by in-flight job (409 banner inline).
- Logout button visible on `/jobs`, `/runs` when content is long.

### Manual verification

After deploy:

1. Click `View manifest` on a phase 11e version — manifest tree visible.
2. Click `View manifest` on a legacy version (if any in DB) — fallback message visible.
3. Click `Logs` on a recent build — log content visible (not `(no output)`).
4. Click `Logs` on a build that failed in `clone` stage — clone container's git-clone error visible.
5. Visit `/jobs` with > 50 jobs — logout still in viewport at bottom-left.
6. Delete a test detector — confirmation requires typing name, list updates.
7. Try deleting while a job is running — 409 banner with cancel-jobs link.

---

## Open Questions

1. **Should A5 ship with A1–A4, or be split out if investigation drags?** Default: bundle. If investigation reveals a maldet-side bug requiring a maldet release, A5 splits into Phase 13a.1 dependent on the maldet PR landing.
2. **Hard delete option for admin?** Out of scope for Phase 13a (Q2 selected option A, soft-only). May revisit in a future phase if compliance / GDPR-style requirement emerges.
3. **`DetectorVersion` listing API for admins to see DELETED rows?** Not needed for current research-lab scale. Versions can be queried via psql if needed.

---

## Appendix A — In-flight job statuses

```python
NON_TERMINAL_JOB_STATUSES = (
    JobStatus.PENDING,    # not yet sent to k8s
    JobStatus.PREPARING,  # k8s pod scheduled, detector container not started
    JobStatus.QUEUED,     # Volcano queue waiting for GPU
    JobStatus.RUNNING,    # detector container running
)
TERMINAL_JOB_STATUSES = (
    JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED,
)
```

Cross-check with `frontend/src/lib/status.ts:NON_TERMINAL_JOB_STATUSES` — must match. Plan task: align if drift.

---

## Appendix B — Confirmed root causes summary

| # | File:line | Root cause |
|---|---|---|
| A1 | `backend/app/schemas/detector.py` `VersionDetailRead.manifest` | non-nullable, legacy rows have NULL → 500 |
| A2 | `backend/app/reconciler.py:613` | hard-coded `container="kaniko"`, real name `"buildkit"` |
| A3 | `frontend/src/routes/_authed.tsx` | `min-h-screen` lets parent grow past viewport |
| A4 | (no bug) | feature missing — UI not wired, no version delete endpoint |
| A5 | TBD via investigation | most likely event-tailer flush on short-lived evaluate pods |
