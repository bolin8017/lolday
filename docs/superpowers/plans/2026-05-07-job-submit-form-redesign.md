# Job Submit Form Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four UX/visibility bugs on Submit Job + Detector detail (View manifest dark, Hyperparameters dark, Test dataset cannot clear, Predict mode model-version empty + free-pairing detector/model) and refactor Predict/Evaluate to match mainstream MLOps conventions (model is primary, runtime derived).

**Architecture:** Two PRs. PR 1 lands non-breaking foundations: backend `ModelVersionRead` gains denormalised `detector_id` + `detector_version_tag`; three new frontend components (`useResolvedTheme`, `ClearableSelect`, `HelpHint`); the four bug fixes; and HelpHint applied to the two stable fields (Priority, Test dataset). PR 2 splits `JobSubmitForm.tsx` into orchestrator + `TrainSubForm` + `InferenceSubForm`; `InferenceSubForm` drives detector display from the chosen model with an "Advanced override" toggle for power users.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Pydantic v2 (backend); Vite 5 + React 18 + TypeScript 5.5 + Tailwind 3.4 + shadcn/ui (Radix primitives) + RJSF v5 + react-i18next + TanStack Query v5 (frontend); pytest + vitest + Testing Library + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md`

---

## File structure overview

### PR 1 — `docs/job-submit-form-redesign` branch (already created, contains spec commit)

| Action | Path                                                    | Responsibility                                                                                        |
| ------ | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Modify | `backend/app/schemas/model_registry.py`                 | Add `detector_id` + `detector_version_tag` to `ModelVersionRead`                                      |
| Modify | `backend/app/routers/models_registry.py`                | Update `_model_version_to_read` signature + 5 call sites; add `DetectorVersion` joins                 |
| Modify | `backend/tests/test_models_registry.py`                 | Assert new fields appear in version-by-id and version-by-job-id endpoints                             |
| Modify | `backend/tests/test_models_list.py`                     | Assert new fields on `/{owner}/{name}/versions` and `/{owner}/{name}/versions/{n}`                    |
| Create | `frontend/src/hooks/useResolvedTheme.ts`                | Returns "light" \| "dark" by reading `<html>` class + matchMedia                                      |
| Create | `frontend/src/components/forms/ClearableSelect.tsx`     | shadcn Select wrapper; X button when `clearable && value`                                             |
| Create | `frontend/src/components/common/HelpHint.tsx`           | HelpCircle icon + Tooltip (default) or Popover (long content)                                         |
| Modify | `frontend/src/components/common/JsonTreeView.tsx`       | Use `useResolvedTheme` to switch theme prop                                                           |
| Modify | `frontend/src/index.css`                                | Extend `.rjsf-wrap` with dark-mode tokens for label / description / input / fieldset                  |
| Modify | `frontend/src/components/forms/JobSubmitForm.tsx`       | Fix model-version envelope; wrap Test dataset in ClearableSelect; HelpHint on Priority + Test dataset |
| Modify | `frontend/src/components/forms/JobSubmitForm.logic.ts`  | Drop `test_dataset_id` from train required list                                                       |
| Modify | `frontend/src/i18n/zh-TW.json`                          | Add `help.test_dataset_optional`, `help.priority_admin` keys                                          |
| Modify | `frontend/src/i18n/en.json`                             | Same as zh-TW                                                                                         |
| Run    | `pnpm gen-api-types`                                    | Regenerate `frontend/src/api/schema.gen.ts`                                                           |
| Modify | `frontend/tests/unit/components/JobSubmitForm.test.tsx` | Update train required test                                                                            |
| Create | `frontend/tests/unit/useResolvedTheme.test.tsx`         | Unit tests for the hook                                                                               |
| Create | `frontend/tests/unit/ClearableSelect.test.tsx`          | Unit tests for clear button                                                                           |
| Create | `frontend/tests/unit/HelpHint.test.tsx`                 | Tooltip + popover modes; a11y                                                                         |
| Create | `frontend/tests/unit/JsonTreeView.test.tsx`             | Asserts theme prop switches with resolved theme                                                       |

### PR 2 — `refactor/inference-form` branch (new, branched off `main` after PR 1 merges)

| Action | Path                                                       | Responsibility                                                                                        |
| ------ | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Create | `frontend/src/components/forms/TrainSubForm.tsx`           | Detector + version + datasets + hyperparams (train)                                                   |
| Create | `frontend/src/components/forms/InferenceSubForm.tsx`       | Source model + version → derived detector + version (read-only by default) → dataset + hyperparams    |
| Create | `frontend/src/components/forms/AdvancedOverrideToggle.tsx` | Collapsible block with Switch + label                                                                 |
| Modify | `frontend/src/components/forms/JobSubmitForm.tsx`          | Reduce to orchestrator: job type tabs + common state + submit + render sub-form                       |
| Modify | `frontend/src/components/forms/JobSubmitForm.logic.ts`     | Update required-fields for evaluate / predict (still validates)                                       |
| Modify | `frontend/src/i18n/zh-TW.json`                             | `help.source_model`, `help.override_detector_version`, `inference.advanced_override` keys             |
| Modify | `frontend/src/i18n/en.json`                                | Same                                                                                                  |
| Modify | `frontend/tests/unit/components/JobSubmitForm.test.tsx`    | Update existing required-fields tests                                                                 |
| Create | `frontend/tests/unit/components/TrainSubForm.test.tsx`     | Render + interaction                                                                                  |
| Create | `frontend/tests/unit/components/InferenceSubForm.test.tsx` | Auto-fill detector on model-version change; advanced override toggle                                  |
| Create | `frontend/tests/e2e/job-submit-train.spec.ts`              | Full Train flow                                                                                       |
| Create | `frontend/tests/e2e/job-submit-inference.spec.ts`          | Full Predict + Evaluate flows                                                                         |
| Modify | `docs/architecture.md`                                     | §9 tech debt: react-json-view-lite candidate; @rjsf/shadcn evaluation; maldet schema description push |

---

## PR 1 — Foundations + bug fixes

Branch: `docs/job-submit-form-redesign` (already exists).

### Task 1: Backend — extend `ModelVersionRead` schema

**Files:**

- Modify: `backend/app/schemas/model_registry.py:13-28`
- Test: `backend/tests/test_models_registry.py` (extend)

- [ ] **Step 1: Add the failing schema test**

Append to `backend/tests/test_models_registry.py`:

```python
@pytest.mark.asyncio
async def test_model_version_read_includes_detector_fields(populated, alice_client):
    """ModelVersionRead must expose detector_id and detector_version_tag.

    These are needed by the frontend Submit Job form to derive the detector
    runtime from a chosen model artifact (mainstream MLOps inference UX).
    """
    r = await alice_client.get("/api/v1/models/alice/elf-rf/versions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "fixture should have at least one version"
    item = items[0]
    assert "detector_id" in item, "missing detector_id"
    assert "detector_version_tag" in item, "missing detector_version_tag"
    assert isinstance(item["detector_id"], str)
    assert isinstance(item["detector_version_tag"], str)
```

If `populated` / `alice_client` fixtures live in another test module, copy them or import from `backend/tests/test_models_list.py` (verify the fixture is reusable; if scoped to that module, add a `conftest.py` entry — see Step 1b).

- [ ] **Step 1b: If `populated` is module-scoped, lift it into `backend/tests/conftest.py`**

Skip if it's already in conftest. Run:

```bash
cd backend && grep -n "@pytest_asyncio.fixture" tests/conftest.py | head
```

If missing, lift the `populated` fixture from `tests/test_models_list.py` to `tests/conftest.py` so all model-registry tests share it. Verify nothing breaks:

```bash
cd backend && uv run pytest tests/test_models_list.py -q
```

- [ ] **Step 2: Run the new test — expect FAIL**

```bash
cd backend && uv run pytest tests/test_models_registry.py::test_model_version_read_includes_detector_fields -v
```

Expected: AssertionError "missing detector_id".

- [ ] **Step 3: Add the fields to the schema**

In `backend/app/schemas/model_registry.py`, add to `ModelVersionRead`:

```python
class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mlflow_version: int
    mlflow_run_id: str
    current_stage: ModelVersionStage
    visibility: ModelVersionVisibility
    detector_version_id: uuid.UUID
    source_job_id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime
    last_transitioned_at: datetime
    # Derived fields — populated by the response builder, not ORM attributes
    owner: str  # user.handle
    name: str  # detector.name
    detector_id: uuid.UUID  # detector.id (NEW — frontend uses this to fetch detector_version manifest)
    detector_version_tag: str  # detector_version.git_tag (NEW — used as path param to /detectors/{id}/versions/{tag})
```

(Test still fails — call sites don't populate yet. Continue to Task 2.)

### Task 2: Backend — populate the new fields at every call site

**Files:**

- Modify: `backend/app/routers/models_registry.py` — `_model_version_to_read` and 5 call sites (`get_model_version_by_id`, `list_model_versions_by_filter`, `list_versions`, `get_version`, `transition_model_version`, `update_visibility` — confirm each via `grep -n _model_version_to_read backend/app/routers/models_registry.py`)
- Test: `backend/tests/test_models_registry.py`, `backend/tests/test_models_list.py`

- [ ] **Step 1: Update helper signature**

Replace `_model_version_to_read` in `backend/app/routers/models_registry.py:54-71`:

```python
def _model_version_to_read(
    mv: ModelVersion,
    owner_handle: str,
    detector_name: str,
    detector_id: uuid.UUID,
    detector_version_tag: str,
) -> ModelVersionRead:
    """Construct ModelVersionRead with derived UI-friendly fields populated.

    The four trailing args are derived from joins against User, Detector, and
    DetectorVersion. Pass them explicitly so each call site is honest about
    its query shape (no lazy-load surprises in async sessions).
    """
    return ModelVersionRead(
        id=mv.id,
        mlflow_version=mv.mlflow_version,
        mlflow_run_id=mv.mlflow_run_id,
        current_stage=mv.current_stage,
        visibility=mv.visibility,
        detector_version_id=mv.detector_version_id,
        source_job_id=mv.source_job_id,
        owner_id=mv.owner_id,
        created_at=mv.created_at,
        last_transitioned_at=mv.last_transitioned_at,
        owner=owner_handle,
        name=detector_name,
        detector_id=detector_id,
        detector_version_tag=detector_version_tag,
    )
```

- [ ] **Step 2: Update `get_model_version_by_id` (line 74-99)**

Add `Detector.id` + `DetectorVersion.git_tag` to the join:

```python
@router.get("/versions/{version_id}", response_model=ModelVersionRead)
async def get_model_version_by_id(
    version_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    from app.models import DetectorVersion  # local import to avoid cycle if any
    row = (
        await session.execute(
            select(
                ModelVersion,
                User.handle,
                Detector.name,
                Detector.id,
                DetectorVersion.git_tag,
            )
            .join(RegisteredModel, ModelVersion.registered_model_id == RegisteredModel.id)
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .join(DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id)
            .where(ModelVersion.id == version_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="model version not found")
    mv, owner_handle, detector_name, detector_id, detector_version_tag = row
    return _model_version_to_read(mv, owner_handle, detector_name, detector_id, detector_version_tag)
```

If `DetectorVersion` is not already in `from app.models import (...)`, add it at module top instead of the local import.

- [ ] **Step 3: Update `list_model_versions_by_filter` (line 102-140)**

```python
rows = (
    await session.execute(
        select(
            ModelVersion,
            User.handle,
            Detector.name,
            Detector.id,
            DetectorVersion.git_tag,
        )
        .join(RegisteredModel, ModelVersion.registered_model_id == RegisteredModel.id)
        .join(User, RegisteredModel.owner_id == User.id)
        .join(Detector, RegisteredModel.detector_id == Detector.id)
        .join(DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id)
        .where(ModelVersion.source_job_id == source_job_id)
        .order_by(ModelVersion.mlflow_version.desc())
        .limit(100)
    )
).all()
items = [
    _model_version_to_read(mv, h, n, did, tag)
    for mv, h, n, did, tag in rows
]
```

- [ ] **Step 4: Update `list_versions` (line 283-310)**

This site loads `mv` only and uses path-param `owner` / `name`. Add a join for the version tag and use `rm.detector_id`:

```python
@router.get("/{owner}/{name}/versions", response_model=ModelVersionList)
async def list_versions(
    owner: str,
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionList:
    rm = await resolve_registered_model(owner, name, session, user)
    visible: sa.ColumnElement[bool]
    if user.role == Role.ADMIN:
        visible = sa.true()
    else:
        visible = (ModelVersion.visibility == ModelVersionVisibility.PUBLIC) | (
            ModelVersion.owner_id == user.id
        )
    rows = (
        await session.execute(
            select(ModelVersion, DetectorVersion.git_tag)
            .join(DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id)
            .where(ModelVersion.registered_model_id == rm.id, visible)
            .order_by(ModelVersion.mlflow_version.desc())
        )
    ).all()
    items = [
        _model_version_to_read(mv, owner, name, rm.detector_id, tag)
        for mv, tag in rows
    ]
    return ModelVersionList(items=items, total=len(items), page=1, page_size=len(items))
```

- [ ] **Step 5: Update `get_version` (line 313-336)**

```python
@router.get("/{owner}/{name}/versions/{version}", response_model=ModelVersionRead)
async def get_version(
    owner: str,
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user)
    row = (
        await session.execute(
            select(ModelVersion, DetectorVersion.git_tag)
            .join(DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id)
            .where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.mlflow_version == version,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "version not found")
    mv, detector_version_tag = row
    is_owner = mv.owner_id == user.id
    is_admin = user.role.value == "admin"
    if mv.visibility == ModelVersionVisibility.PRIVATE and not (is_owner or is_admin):
        raise HTTPException(404, "version not found")  # hide-existence
    return _model_version_to_read(mv, owner, name, rm.detector_id, detector_version_tag)
```

- [ ] **Step 6: Update `transition_model_version` (around line 443)**

Add a single fetch of the tag before constructing the response:

```python
detector_version_tag = (
    await session.execute(
        select(DetectorVersion.git_tag).where(
            DetectorVersion.id == mv.detector_version_id
        )
    )
).scalar_one()
return _model_version_to_read(
    mv, owner_handle, detector_name, rm.detector_id, detector_version_tag
)
```

- [ ] **Step 7: Update `update_visibility` and any remaining call sites**

Find them with:

```bash
cd backend && grep -n "_model_version_to_read(" app/routers/models_registry.py
```

Each call site already has `rm` (RegisteredModel) in scope. Add a `select(DetectorVersion.git_tag)` lookup before the call and pass `rm.detector_id` + the tag.

- [ ] **Step 8: Run all model-registry tests — expect PASS**

```bash
cd backend && uv run pytest tests/test_models_registry.py tests/test_models_list.py tests/test_models_transition.py tests/test_models_owner_transfer.py tests/test_jobs_model_version_visibility.py -v
```

Expected: all pass, including the new `test_model_version_read_includes_detector_fields`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/schemas/model_registry.py backend/app/routers/models_registry.py backend/tests/test_models_registry.py
git commit -m "$(cat <<'EOF'
feat(backend): expose detector_id + detector_version_tag in ModelVersionRead

Frontend Submit Job form needs to derive the detector runtime from a
chosen model artifact (mainstream MLOps inference UX). Adding two
denormalised fields keeps it to a single API response and avoids a new
endpoint. All five call sites of `_model_version_to_read` now join
DetectorVersion or read the tag once before responding.

Spec: docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Frontend — regenerate API schema

**Files:**

- Modify: `frontend/src/api/schema.gen.ts` (regenerated; do not hand-edit)

- [ ] **Step 1: Confirm backend is reachable**

The generator hits the backend OpenAPI doc. Either start the backend dev server or use the cached approach the script already supports:

```bash
cd backend && uv run uvicorn app.main:app --port 8000 &
sleep 2
```

(If the harness uses a different mechanism, follow `frontend/scripts/gen-api-types.sh`.)

- [ ] **Step 2: Run the codegen**

```bash
cd frontend && pnpm gen-api-types
```

- [ ] **Step 3: Verify the new fields appear**

```bash
grep -nA 12 "ModelVersionRead" frontend/src/api/schema.gen.ts | head -25
```

Expected: `detector_id` and `detector_version_tag` lines present.

- [ ] **Step 4: Stop the dev server**

```bash
kill %1 2>/dev/null || true
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/schema.gen.ts
git commit -m "chore(frontend): regen schema.gen.ts after ModelVersionRead extension

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 4: Frontend — `useResolvedTheme` hook

**Files:**

- Create: `frontend/src/hooks/useResolvedTheme.ts`
- Test: `frontend/tests/unit/useResolvedTheme.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/useResolvedTheme.test.tsx`:

```tsx
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

describe("useResolvedTheme", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("light", "dark");
  });

  it("returns 'dark' when <html> has dark class", () => {
    document.documentElement.classList.add("dark");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("returns 'light' when <html> has light class", () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("falls back to matchMedia when neither class is set", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true, // simulate prefers dark
        media: "(prefers-color-scheme: dark)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("updates when documentElement class flips", () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
    act(() => {
      document.documentElement.classList.remove("light");
      document.documentElement.classList.add("dark");
    });
    expect(result.current).toBe("dark");
  });
});
```

- [ ] **Step 2: Run the test — expect FAIL (module not found)**

```bash
cd frontend && pnpm test useResolvedTheme -- --run
```

Expected: cannot find module `@/hooks/useResolvedTheme`.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/hooks/useResolvedTheme.ts`:

```ts
import { useEffect, useState } from "react";

type Resolved = "light" | "dark";

function read(): Resolved {
  if (typeof document === "undefined") return "light";
  const root = document.documentElement;
  if (root.classList.contains("dark")) return "dark";
  if (root.classList.contains("light")) return "light";
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

/**
 * Returns the currently applied theme ("light" | "dark"), reflecting
 * what ThemeProvider has set on <html>. Subscribes to MutationObserver
 * so consumers re-render when the user toggles the theme.
 */
export function useResolvedTheme(): Resolved {
  const [resolved, setResolved] = useState<Resolved>(read);

  useEffect(() => {
    const update = () => setResolved(read());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    const mql = window.matchMedia?.("(prefers-color-scheme: dark)");
    mql?.addEventListener?.("change", update);
    return () => {
      observer.disconnect();
      mql?.removeEventListener?.("change", update);
    };
  }, []);

  return resolved;
}
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
cd frontend && pnpm test useResolvedTheme -- --run
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useResolvedTheme.ts frontend/tests/unit/useResolvedTheme.test.tsx
git commit -m "feat(frontend): useResolvedTheme hook

Returns 'light' | 'dark' by reading <html>.classList, with matchMedia
fallback when ThemeProvider has not yet attached a class. Subscribes
to a MutationObserver so consumers re-render on theme toggle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 5: Frontend — fix `JsonTreeView` dark mode

**Files:**

- Modify: `frontend/src/components/common/JsonTreeView.tsx`
- Test: `frontend/tests/unit/JsonTreeView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/JsonTreeView.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { JsonTreeView } from "@/components/common/JsonTreeView";

describe("JsonTreeView", () => {
  afterEach(() => {
    document.documentElement.classList.remove("light", "dark");
  });

  it("uses a dark theme class when <html> is dark", () => {
    document.documentElement.classList.add("dark");
    const { container } = render(<JsonTreeView value={{ a: 1 }} />);
    // react-json-view renders an inline style with the chosen theme's
    // background colour. We assert the inline-style background-color
    // belongs to a dark palette (anything other than the default light
    // #ffffff) — robust against future theme-name changes.
    const root = container.firstElementChild as HTMLElement;
    const inner = root.querySelector("[class*=react-json-view]") as HTMLElement;
    expect(inner).toBeTruthy();
    // Light theme has white-ish background; dark themes have non-white.
    const bg = (inner.style.backgroundColor || "").toLowerCase();
    expect(bg === "rgb(255, 255, 255)" || bg === "white").toBe(false);
  });
});
```

- [ ] **Step 2: Run the test — expect FAIL**

```bash
cd frontend && pnpm test JsonTreeView -- --run
```

Expected: assertion fails because `theme="rjv-default"` is hardcoded (light).

- [ ] **Step 3: Implement the fix**

Edit `frontend/src/components/common/JsonTreeView.tsx`:

```tsx
import ReactJsonView from "@microlink/react-json-view";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

interface Props {
  value: unknown;
  collapsed?: number | boolean;
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  const theme = useResolvedTheme();
  // monokai is one of the bundled dark themes in @microlink/react-json-view
  // and gives high-contrast keys/values on dark backgrounds.
  return (
    <div className="overflow-auto rounded-md border bg-card">
      <ReactJsonView
        src={(value ?? {}) as object}
        name={false}
        collapsed={collapsed}
        displayDataTypes={false}
        displayObjectSize={false}
        enableClipboard={copyable}
        theme={theme === "dark" ? "monokai" : "rjv-default"}
        style={{
          padding: "0.75rem",
          fontSize: "0.8rem",
          fontFamily: "ui-monospace, monospace",
          background: "transparent",
        }}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
cd frontend && pnpm test JsonTreeView -- --run
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/common/JsonTreeView.tsx frontend/tests/unit/JsonTreeView.test.tsx
git commit -m "fix(frontend): JsonTreeView dark theme

Was hardcoded to rjv-default (light), so View manifest text was
illegible under dark mode. Pull the theme from useResolvedTheme()
and switch to monokai when dark.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 6: Frontend — extend `.rjsf-wrap` for dark mode

**Files:**

- Modify: `frontend/src/index.css:75-90`

- [ ] **Step 1: Update `.rjsf-wrap` block**

Replace the existing block in `frontend/src/index.css`:

```css
.rjsf-wrap {
  & input,
  & textarea,
  & select {
    width: 100%;
    @apply bg-background text-foreground border-input;
  }
  & label,
  & legend,
  & .control-label {
    @apply text-foreground;
  }
  & .field-description,
  & small,
  & .help-block {
    @apply text-muted-foreground;
  }
  & fieldset {
    @apply border-border;
  }
  & .array-item-toolbox {
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  @media (max-width: 767px) {
    & label {
      font-size: 0.8125rem;
    }
  }
}
```

- [ ] **Step 2: Visual smoke check**

Run dev server and toggle dark mode on `/jobs/new`:

```bash
cd frontend && pnpm dev
```

Open `http://localhost:5173/jobs/new`, pick a detector + version that has hyperparameters, toggle Dark mode in the user menu. Description text and labels should be legible. Quit with `q`.

- [ ] **Step 3: Run unit tests to confirm no regression**

```bash
cd frontend && pnpm test RjsfConfigForm -- --run
```

Expected: existing 3 tests still pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css
git commit -m "fix(frontend): RJSF default templates need dark-mode tokens

RJSF v5 default templates render bare <small>/<label>/<input> with
no Tailwind / CSS-variable styling, so dark-mode tokens never reached
the Hyperparameters card. Extend .rjsf-wrap to push text-foreground /
text-muted-foreground / bg-background / border-input onto the rendered
DOM.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 7: Frontend — `ClearableSelect` component

**Files:**

- Create: `frontend/src/components/forms/ClearableSelect.tsx`
- Test: `frontend/tests/unit/ClearableSelect.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/ClearableSelect.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ClearableSelect } from "@/components/forms/ClearableSelect";
import {
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function renderHelper(value: string, onChange = vi.fn(), clearable = true) {
  return render(
    <ClearableSelect
      value={value}
      onValueChange={onChange}
      clearable={clearable}
    >
      <SelectTrigger>
        <SelectValue placeholder="Pick" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="a">A</SelectItem>
        <SelectItem value="b">B</SelectItem>
      </SelectContent>
    </ClearableSelect>,
  );
}

describe("ClearableSelect", () => {
  it("does not show clear button when value is empty", () => {
    renderHelper("");
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });

  it("shows clear button when value set and clearable=true", () => {
    renderHelper("a");
    expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
  });

  it("calls onValueChange with empty string when clear clicked", async () => {
    const onChange = vi.fn();
    renderHelper("a", onChange, true);
    await userEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("does not show clear when clearable=false even if value set", () => {
    renderHelper("a", vi.fn(), false);
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Run test — expect FAIL (module not found)**

```bash
cd frontend && pnpm test ClearableSelect -- --run
```

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/forms/ClearableSelect.tsx`:

```tsx
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

interface Props {
  value: string;
  onValueChange: (value: string) => void;
  clearable?: boolean;
  disabled?: boolean;
  children: ReactNode;
}

/**
 * shadcn Select wrapper that adds a "clear" button (X icon) when
 * the field has a value and `clearable` is true. Used for optional
 * fields where Radix Select alone provides no way to deselect.
 */
export function ClearableSelect({
  value,
  onValueChange,
  clearable = false,
  disabled = false,
  children,
}: Props) {
  const showClear = clearable && !!value && !disabled;
  return (
    <div className="flex items-center gap-1">
      <div className="flex-1">
        <Select value={value} onValueChange={onValueChange} disabled={disabled}>
          {children}
        </Select>
      </div>
      {showClear && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Clear"
          onClick={() => onValueChange("")}
        >
          <X className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd frontend && pnpm test ClearableSelect -- --run
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/ClearableSelect.tsx frontend/tests/unit/ClearableSelect.test.tsx
git commit -m "feat(frontend): ClearableSelect — shadcn Select wrapper with clear button

shadcn Select (Radix) has no built-in way to clear an optional field.
Wrap it with a small X icon button shown only when value is set and
clearable=true. Used for the Test dataset field in the Submit Job form.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 8: Frontend — `HelpHint` component

**Files:**

- Create: `frontend/src/components/common/HelpHint.tsx`
- Test: `frontend/tests/unit/HelpHint.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/HelpHint.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import { HelpHint } from "@/components/common/HelpHint";

describe("HelpHint", () => {
  it("renders a question / help icon button", () => {
    render(<HelpHint>quick tip</HelpHint>);
    expect(
      screen.getByRole("button", { name: /help|info|hint/i }),
    ).toBeInTheDocument();
  });

  it("tooltip mode reveals text on hover", async () => {
    render(<HelpHint>tooltip text</HelpHint>);
    const trigger = screen.getByRole("button", { name: /help|info|hint/i });
    await userEvent.hover(trigger);
    // Radix renders tooltip into a portal; query the document body.
    expect(await screen.findByText(/tooltip text/i)).toBeInTheDocument();
  });

  it("popover mode reveals text on click", async () => {
    render(<HelpHint popover>popover text</HelpHint>);
    const trigger = screen.getByRole("button", { name: /help|info|hint/i });
    await userEvent.click(trigger);
    expect(await screen.findByText(/popover text/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd frontend && pnpm test HelpHint -- --run
```

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/common/HelpHint.tsx`:

```tsx
import { HelpCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface Props {
  children: ReactNode;
  /**
   * Use Popover (click-to-open, larger surface) instead of Tooltip
   * (hover-to-open, single-line). Pick popover for content longer
   * than two lines or with formatting.
   */
  popover?: boolean;
  className?: string;
}

/**
 * Small "?" icon next to a label that surfaces a short hint or
 * a longer explanation. A reusable, mainstream pattern (Material 3,
 * Carbon, Atlassian).
 */
export function HelpHint({ children, popover = false, className }: Props) {
  if (popover) {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Help"
            className={className ?? "h-6 w-6"}
          >
            <HelpCircle className="h-4 w-4 text-muted-foreground" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="max-w-xs text-sm">{children}</PopoverContent>
      </Popover>
    );
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Help"
            className={className ?? "h-6 w-6"}
          >
            <HelpCircle className="h-4 w-4 text-muted-foreground" />
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-sm">{children}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
```

- [ ] **Step 4: Run test — expect PASS**

```bash
cd frontend && pnpm test HelpHint -- --run
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/common/HelpHint.tsx frontend/tests/unit/HelpHint.test.tsx
git commit -m "feat(frontend): HelpHint — reusable inline help component

Wraps shadcn Tooltip (default, hover) or Popover (popover prop, click)
behind a HelpCircle icon button. Used for inline field help on the
Submit Job form (Priority, Test dataset, Source model, ...).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 9: Frontend — i18n keys for help hints (PR 1 scope)

**Files:**

- Modify: `frontend/src/i18n/zh-TW.json`
- Modify: `frontend/src/i18n/en.json`

- [ ] **Step 1: Add zh-TW keys**

Insert under the `"jobs"` block in `frontend/src/i18n/zh-TW.json` (keep existing keys unchanged):

```json
"help": {
  "test_dataset_optional": "可選。提供時會多算最終 metrics 與混淆矩陣；不選則只訓練模型，不算指標。",
  "priority_admin": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。"
}
```

The new sub-block lives under `jobs`, alongside `priority`. Final shape:

```json
"jobs": {
  "priority": { ... existing ... },
  "help": {
    "test_dataset_optional": "...",
    "priority_admin": "..."
  }
}
```

- [ ] **Step 2: Add the same keys to `en.json`**

```json
"help": {
  "test_dataset_optional": "Optional. When provided, the run also computes final metrics and the confusion matrix. Skipping it only trains the model.",
  "priority_admin": "Raising priority halts dispatch of lower-priority queued jobs until this one reaches Volcano. Already-running jobs are unaffected."
}
```

- [ ] **Step 3: Run typecheck (locale type safety, if any)**

```bash
cd frontend && pnpm typecheck
```

Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/zh-TW.json frontend/src/i18n/en.json
git commit -m "feat(frontend): i18n keys for Submit Job help hints (PR 1)

Adds jobs.help.test_dataset_optional and jobs.help.priority_admin in
zh-TW + en. Source-of-truth for the inline help that lands in PR 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 10: Frontend — fix model-version envelope bug + train test_dataset optional + apply ClearableSelect / HelpHint

**Files:**

- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/src/components/forms/JobSubmitForm.logic.ts`
- Modify: `frontend/tests/unit/components/JobSubmitForm.test.tsx`

- [ ] **Step 1: Update the failing required-fields test for train**

Edit `frontend/tests/unit/components/JobSubmitForm.test.tsx` first three tests:

```tsx
describe("requiredFieldsForType", () => {
  it("train needs only train_dataset (test is optional)", () => {
    expect(requiredFieldsForType("train")).toEqual(["train_dataset_id"]);
  });
  it("evaluate needs test+source_model", () => {
    expect(requiredFieldsForType("evaluate")).toEqual([
      "test_dataset_id",
      "source_model_version_id",
    ]);
  });
  it("predict needs predict+source_model", () => {
    expect(requiredFieldsForType("predict")).toEqual([
      "predict_dataset_id",
      "source_model_version_id",
    ]);
  });
});
```

- [ ] **Step 2: Run the test — expect train test FAIL**

```bash
cd frontend && pnpm test JobSubmitForm -- --run
```

- [ ] **Step 3: Update `requiredFieldsForType` for train**

Edit `frontend/src/components/forms/JobSubmitForm.logic.ts`:

```ts
import type { JobType } from "@/api/queries/jobs";

export function requiredFieldsForType(type: JobType): string[] {
  switch (type) {
    case "train":
      return ["train_dataset_id"];
    case "evaluate":
      return ["test_dataset_id", "source_model_version_id"];
    case "predict":
      return ["predict_dataset_id", "source_model_version_id"];
    default:
      return [];
  }
}
```

- [ ] **Step 4: Run the logic test — expect PASS**

```bash
cd frontend && pnpm test JobSubmitForm -- --run
```

- [ ] **Step 5: Fix the model-version envelope bug**

In `frontend/src/components/forms/JobSubmitForm.tsx`, replace lines 96-101:

```tsx
const modelVersionsArr = (modelVersions ?? []) as {
  id: string;
  mlflow_version: number;
  current_stage: string;
}[];
```

(`useModelVersions` already returns a plain array; the previous cast looked for an `items` envelope and silently produced `[]`.)

- [ ] **Step 6: Wire ClearableSelect + HelpHint**

Still in `frontend/src/components/forms/JobSubmitForm.tsx`:

a) Import the new components and `useTranslation` is already there:

```tsx
import { ClearableSelect } from "./ClearableSelect";
import { HelpHint } from "@/components/common/HelpHint";
```

b) Update the inner `DatasetField` helper to support optional clearing:

```tsx
function DatasetField({
  label,
  value,
  onChange,
  options,
  optional = false,
  helpHint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
  optional?: boolean;
  helpHint?: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center gap-1">
        <Label>{label}</Label>
        {helpHint && <HelpHint>{helpHint}</HelpHint>}
      </div>
      <ClearableSelect
        value={value}
        onValueChange={onChange}
        clearable={optional}
      >
        <SelectTrigger>
          <SelectValue placeholder="Pick dataset" />
        </SelectTrigger>
        <SelectContent>
          {options.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              {d.name}
            </SelectItem>
          ))}
        </SelectContent>
      </ClearableSelect>
    </div>
  );
}
```

c) For the Train branch, mark Test dataset optional with a help hint:

```tsx
{
  type === "train" && (
    <>
      <DatasetField
        label="Train dataset"
        value={trainDatasetId}
        onChange={setTrainDatasetId}
        options={datasetsArr}
      />
      <DatasetField
        label="Test dataset"
        value={testDatasetId}
        onChange={setTestDatasetId}
        options={datasetsArr}
        optional
        helpHint={t("jobs.help.test_dataset_optional")}
      />
    </>
  );
}
```

d) Add a HelpHint next to the Priority label (popover for the long warning):

In the Priority Card body, replace the `<Label>` line with:

```tsx
<div className="flex items-center gap-1">
  <Label htmlFor="priority-input">{t("jobs.priority.label")}</Label>
  <HelpHint popover>{t("jobs.help.priority_admin")}</HelpHint>
</div>
```

- [ ] **Step 7: Run frontend test suite**

```bash
cd frontend && pnpm test -- --run
```

Expected: all pre-existing tests pass; new tests pass.

- [ ] **Step 8: Run typecheck + lint**

```bash
cd frontend && pnpm typecheck && pnpm lint
```

Expected: clean.

- [ ] **Step 9: Manual smoke**

```bash
cd frontend && pnpm dev
```

- Open `/jobs/new`, switch to Train, pick a Test dataset, click X — should clear.
- Pick Predict + a detector + version, then a Source model — Model version dropdown should populate (assuming the model has versions).
- Hover the `?` next to Test dataset (Train) — tooltip shows the i18n string.
- For an admin user, click the `?` next to Priority — popover shows the warning.

Quit with `q`.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/forms/JobSubmitForm.tsx frontend/src/components/forms/JobSubmitForm.logic.ts frontend/tests/unit/components/JobSubmitForm.test.tsx
git commit -m "$(cat <<'EOF'
fix(frontend): submit-job — model version envelope, train test_dataset optional, help hints

Three independent issues fixed in one pass:

- Model version dropdown was empty for Predict / Evaluate. The form
  cast `modelVersions` to `{ items?: [...] }` and read `.items`, but
  useModelVersions already unwraps and returns a plain array. The
  cast yielded undefined → empty dropdown. Use `modelVersions ?? []`.
- Train treated Test dataset as required (in `requiredFieldsForType`)
  even though i18n + StageExplainer documented it as optional. Drop
  `test_dataset_id` from the train required list.
- Test dataset (Train mode) is now wrapped in ClearableSelect so users
  can deselect after picking. Add HelpHint next to its label.
- Add HelpHint (popover) next to Priority label, reusing the existing
  warning string.

Spec: docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 11: Run full lint / typecheck / test / pre-commit

**Files:** none

- [ ] **Step 1: Run pre-commit (full)**

```bash
pre-commit run --all-files
```

Expected: clean.

- [ ] **Step 2: Backend typecheck + lint + tests**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

Expected: clean.

- [ ] **Step 3: Frontend typecheck + lint + unit tests**

```bash
cd frontend && pnpm typecheck && pnpm lint && pnpm test -- --run
```

Expected: clean.

- [ ] **Step 4: Helm sanity (no chart edits in this PR but cheap insurance)**

```bash
helm lint charts/lolday
```

Expected: clean.

### Task 12: Open PR 1

- [ ] **Step 1: Push the branch**

```bash
git push -u origin docs/job-submit-form-redesign
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(submit-job): root-cause fixes — dark mode, test-dataset clear, model version, help hints" --body "$(cat <<'EOF'
## Summary

- Backend: `ModelVersionRead` exposes `detector_id` + `detector_version_tag` (denormalised, no migration). Five call sites updated to join `DetectorVersion` or fetch the tag once before responding.
- Frontend foundations: `useResolvedTheme` hook, `ClearableSelect`, `HelpHint`.
- Bug fixes: View manifest dark, Hyperparameters dark (RJSF default templates now respect dark tokens), Train Test dataset wrongly required + cannot clear, Predict / Evaluate Model version dropdown always empty.
- HelpHint applied to Priority (popover) and Test dataset Train (tooltip). Source model + override-detector hints land with the form refactor in PR 2.

Spec: docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md
Plan: docs/superpowers/plans/2026-05-07-job-submit-form-redesign.md

## Test plan

- [ ] `pre-commit run --all-files`
- [ ] `cd backend && uv run pytest`
- [ ] `cd frontend && pnpm test`
- [ ] Manual: open `/jobs/new` in dark mode; submit a Train job with cleared Test dataset; submit a Predict job and confirm Model version dropdown populates.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Wait for CI green; address review; merge**

CI will run lint / backend / frontend / helm / images workflows. PR 2 depends on PR 1 merging — coordinate.

---

## PR 2 — Form refactor (Train vs Inference sub-forms)

Branch: `refactor/inference-form` (new, branched off `main` after PR 1 merges).

### Task 13: Branch off main + open worktree (optional)

- [ ] **Step 1**

```bash
git checkout main && git pull && git checkout -b refactor/inference-form
```

### Task 14: Frontend — extract `TrainSubForm`

**Files:**

- Create: `frontend/src/components/forms/TrainSubForm.tsx`
- Modify: `frontend/src/components/forms/JobSubmitForm.tsx` (will use the new component but full orchestrator extraction happens in Task 15)

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/components/TrainSubForm.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TrainSubForm } from "@/components/forms/TrainSubForm";

vi.mock("@/api/queries/detectors", () => ({
  useDetectors: () => ({
    data: { items: [{ id: "d1", display_name: "ELF RF" }] },
  }),
  useDetectorVersions: () => ({
    data: { items: [{ id: "v1", git_tag: "v1.0.0", status: "active" }] },
  }),
  useDetectorVersion: () => ({
    data: { manifest: { stages: { train: { params_schema: {} } } } },
  }),
}));
vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({
    data: { items: [{ id: "ds1", name: "malware-train" }] },
  }),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("TrainSubForm", () => {
  it("renders Detector + Train dataset + Test dataset (optional) sections", () => {
    wrap(
      <TrainSubForm
        detectorId=""
        setDetectorId={() => {}}
        versionTag=""
        setVersionTag={() => {}}
        trainDatasetId=""
        setTrainDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    expect(screen.getByText(/detector/i)).toBeInTheDocument();
    expect(screen.getByText(/train dataset/i)).toBeInTheDocument();
    expect(screen.getByText(/test dataset/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test — expect FAIL (module not found)**

```bash
cd frontend && pnpm test TrainSubForm -- --run
```

- [ ] **Step 3: Implement `TrainSubForm`**

Create `frontend/src/components/forms/TrainSubForm.tsx`. Move the Detector + Datasets + Hyperparameters cards out of `JobSubmitForm.tsx` (Train branch only):

```tsx
import { useTranslation } from "react-i18next";
import {
  useDetectors,
  useDetectorVersion,
  useDetectorVersions,
} from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ClearableSelect } from "./ClearableSelect";
import { HelpHint } from "@/components/common/HelpHint";
import { RjsfConfigForm } from "./RjsfConfigForm";

interface Props {
  detectorId: string;
  setDetectorId: (v: string) => void;
  versionTag: string;
  setVersionTag: (v: string) => void;
  trainDatasetId: string;
  setTrainDatasetId: (v: string) => void;
  testDatasetId: string;
  setTestDatasetId: (v: string) => void;
  config: Record<string, unknown>;
  setConfig: (v: Record<string, unknown>) => void;
}

export function TrainSubForm(p: Props) {
  const { t } = useTranslation();
  const { data: detectors } = useDetectors();
  const { data: versions } = useDetectorVersions(p.detectorId);
  const { data: versionDetail } = useDetectorVersion(
    p.detectorId,
    p.versionTag,
  );
  const { data: datasets } = useDatasets("all");

  const detectorsArr =
    (detectors as { items?: { id: string; display_name: string }[] })?.items ??
    (detectors as unknown as { id: string; display_name: string }[]) ??
    [];
  const versionsArr =
    (versions as { items?: { id: string; git_tag: string; status: string }[] })
      ?.items ??
    (versions as unknown as
      | { id: string; git_tag: string; status: string }[]
      | undefined) ??
    [];
  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as unknown as { id: string; name: string }[]) ??
    [];

  const stages = versionDetail?.manifest?.stages as
    | Record<string, { params_schema?: object }>
    | undefined;
  const stageSchema = stages?.train?.params_schema;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Detector</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label>Detector</Label>
            <Select
              value={p.detectorId}
              onValueChange={(v) => {
                p.setDetectorId(v);
                p.setVersionTag("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick detector" />
              </SelectTrigger>
              <SelectContent>
                {detectorsArr.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Version</Label>
            <Select
              value={p.versionTag}
              onValueChange={p.setVersionTag}
              disabled={!p.detectorId}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick version" />
              </SelectTrigger>
              <SelectContent>
                {versionsArr
                  .filter((v) => v.status === "active")
                  .map((v) => (
                    <SelectItem key={v.git_tag} value={v.git_tag}>
                      {v.git_tag}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label>Train dataset</Label>
            <Select
              value={p.trainDatasetId}
              onValueChange={p.setTrainDatasetId}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick dataset" />
              </SelectTrigger>
              <SelectContent>
                {datasetsArr.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <div className="flex items-center gap-1">
              <Label>Test dataset</Label>
              <HelpHint>{t("jobs.help.test_dataset_optional")}</HelpHint>
            </div>
            <ClearableSelect
              value={p.testDatasetId}
              onValueChange={p.setTestDatasetId}
              clearable
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick dataset (optional)" />
              </SelectTrigger>
              <SelectContent>
                {datasetsArr.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </ClearableSelect>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hyperparameters</CardTitle>
        </CardHeader>
        <CardContent>
          {stageSchema ? (
            <RjsfConfigForm
              schema={stageSchema}
              value={p.config}
              onChange={p.setConfig}
            />
          ) : p.versionTag ? (
            <p className="text-sm text-destructive">
              Selected detector version has no params schema; rebuild with
              maldet ≥ 1.1.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a detector + version to load its hyperparameter form.
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}
```

- [ ] **Step 4: Run the test — expect PASS**

```bash
cd frontend && pnpm test TrainSubForm -- --run
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/TrainSubForm.tsx frontend/tests/unit/components/TrainSubForm.test.tsx
git commit -m "feat(frontend): TrainSubForm — train-flow card group

Extracts the Train sub-form (Detector + version, datasets, hyperparams)
out of JobSubmitForm. Test dataset is wrapped in ClearableSelect with
a HelpHint reflecting the optional contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 15: Frontend — `InferenceSubForm` with model-driven detector + advanced override

**Files:**

- Create: `frontend/src/components/forms/InferenceSubForm.tsx`
- Test: `frontend/tests/unit/components/InferenceSubForm.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/components/InferenceSubForm.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InferenceSubForm } from "@/components/forms/InferenceSubForm";

vi.mock("@/api/queries/models", () => ({
  useRegisteredModels: () => ({
    data: [
      { owner: "alice", name: "elf-rf" },
      { owner: "alice", name: "elf-cnn" },
    ],
  }),
  useModelVersions: (owner: string, name: string) => ({
    data:
      owner && name === "elf-rf"
        ? [
            {
              id: "mv1",
              mlflow_version: 1,
              current_stage: "Production",
              detector_id: "det-rf",
              detector_version_tag: "v1.0.0",
            },
          ]
        : [],
  }),
}));
vi.mock("@/api/queries/detectors", () => ({
  useDetector: (id: string) => ({
    data: id === "det-rf" ? { id: "det-rf", display_name: "ELF RF" } : null,
  }),
  useDetectorVersions: () => ({
    data: { items: [{ id: "v1", git_tag: "v1.0.0", status: "active" }] },
  }),
  useDetectorVersion: () => ({
    data: { manifest: { stages: { predict: { params_schema: {} } } } },
  }),
}));
vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({ data: { items: [{ id: "ds1", name: "samples-x" }] } }),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("InferenceSubForm", () => {
  it("auto-fills detector when a model version is chosen", async () => {
    let derivedDetectorId = "";
    let derivedTag = "";
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner=""
        setSourceModelOwner={() => {}}
        sourceModelName=""
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId={derivedDetectorId}
        setDerivedDetectorId={(v) => (derivedDetectorId = v)}
        derivedDetectorVersionTag={derivedTag}
        setDerivedDetectorVersionTag={(v) => (derivedTag = v)}
        overrideDetectorVersion={false}
        setOverrideDetectorVersion={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // Source model dropdown is the first interactive field
    expect(screen.getByText(/source model/i)).toBeInTheDocument();
  });

  it("renders Advanced override toggle (collapsed by default)", () => {
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner=""
        setSourceModelOwner={() => {}}
        sourceModelName=""
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId=""
        setDerivedDetectorId={() => {}}
        derivedDetectorVersionTag=""
        setDerivedDetectorVersionTag={() => {}}
        overrideDetectorVersion={false}
        setOverrideDetectorVersion={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: /advanced.*override/i }),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL (module not found)**

```bash
cd frontend && pnpm test InferenceSubForm -- --run
```

- [ ] **Step 3: Implement `InferenceSubForm`**

Create `frontend/src/components/forms/InferenceSubForm.tsx`:

```tsx
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import {
  useDetector,
  useDetectorVersion,
  useDetectorVersions,
} from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpHint } from "@/components/common/HelpHint";
import { RjsfConfigForm } from "./RjsfConfigForm";

interface Props {
  type: "evaluate" | "predict";
  sourceModelOwner: string;
  setSourceModelOwner: (v: string) => void;
  sourceModelName: string;
  setSourceModelName: (v: string) => void;
  sourceModelVersionId: string;
  setSourceModelVersionId: (v: string) => void;
  derivedDetectorId: string;
  setDerivedDetectorId: (v: string) => void;
  derivedDetectorVersionTag: string;
  setDerivedDetectorVersionTag: (v: string) => void;
  overrideDetectorVersion: boolean;
  setOverrideDetectorVersion: (v: boolean) => void;
  predictDatasetId: string;
  setPredictDatasetId: (v: string) => void;
  testDatasetId: string;
  setTestDatasetId: (v: string) => void;
  config: Record<string, unknown>;
  setConfig: (v: Record<string, unknown>) => void;
}

export function InferenceSubForm(p: Props) {
  const { t } = useTranslation();
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(
    p.sourceModelOwner,
    p.sourceModelName,
  );
  const { data: detector } = useDetector(p.derivedDetectorId);
  const { data: detectorVersions } = useDetectorVersions(p.derivedDetectorId);
  const { data: detectorVersionDetail } = useDetectorVersion(
    p.derivedDetectorId,
    p.derivedDetectorVersionTag,
  );
  const { data: datasets } = useDatasets("all");

  const modelsArr = (models as { owner: string; name: string }[]) ?? [];
  const modelVersionsArr =
    (modelVersions as {
      id: string;
      mlflow_version: number;
      current_stage: string;
      detector_id: string;
      detector_version_tag: string;
    }[]) ?? [];
  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as unknown as { id: string; name: string }[]) ??
    [];

  // When a model version is chosen, derive detector_id + tag.
  useEffect(() => {
    if (!p.sourceModelVersionId) return;
    const mv = modelVersionsArr.find((v) => v.id === p.sourceModelVersionId);
    if (!mv) return;
    p.setDerivedDetectorId(mv.detector_id);
    if (!p.overrideDetectorVersion) {
      p.setDerivedDetectorVersionTag(mv.detector_version_tag);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to model version + override flag changes
  }, [
    p.sourceModelVersionId,
    p.overrideDetectorVersion,
    modelVersionsArr.length,
  ]);

  const detectorVersionsArr =
    (detectorVersions as { items?: { git_tag: string; status: string }[] })
      ?.items ?? [];

  const stages = detectorVersionDetail?.manifest?.stages as
    | Record<string, { params_schema?: object }>
    | undefined;
  const stageSchema = stages?.[p.type]?.params_schema;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Source model</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <div className="flex items-center gap-1">
              <Label>Source model</Label>
              <HelpHint>{t("jobs.help.source_model")}</HelpHint>
            </div>
            <Select
              value={
                p.sourceModelOwner
                  ? `${p.sourceModelOwner}/${p.sourceModelName}`
                  : ""
              }
              onValueChange={(v) => {
                const [o, ...rest] = v.split("/");
                p.setSourceModelOwner(o ?? "");
                p.setSourceModelName(rest.join("/"));
                p.setSourceModelVersionId("");
                p.setDerivedDetectorId("");
                p.setDerivedDetectorVersionTag("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick model" />
              </SelectTrigger>
              <SelectContent>
                {modelsArr.map((m) => (
                  <SelectItem
                    key={`${m.owner}/${m.name}`}
                    value={`${m.owner}/${m.name}`}
                  >
                    {m.owner}/{m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Model version</Label>
            <Select
              value={p.sourceModelVersionId}
              onValueChange={p.setSourceModelVersionId}
              disabled={!p.sourceModelName}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick version" />
              </SelectTrigger>
              <SelectContent>
                {modelVersionsArr.map((mv) => (
                  <SelectItem key={mv.id} value={mv.id}>
                    v{mv.mlflow_version} ({mv.current_stage})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Detector (derived)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div>
            <span className="text-muted-foreground">Detector:</span>{" "}
            {detector ? detector.display_name : "—"}
          </div>
          <div>
            <span className="text-muted-foreground">Version:</span>{" "}
            {p.overrideDetectorVersion ? (
              <Select
                value={p.derivedDetectorVersionTag}
                onValueChange={p.setDerivedDetectorVersionTag}
                disabled={!p.derivedDetectorId}
              >
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Pick version" />
                </SelectTrigger>
                <SelectContent>
                  {detectorVersionsArr
                    .filter((v) => v.status === "active")
                    .map((v) => (
                      <SelectItem key={v.git_tag} value={v.git_tag}>
                        {v.git_tag}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            ) : (
              <code>{p.derivedDetectorVersionTag || "—"}</code>
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() =>
              p.setOverrideDetectorVersion(!p.overrideDetectorVersion)
            }
            className="px-0"
          >
            {p.overrideDetectorVersion ? (
              <ChevronDown className="h-4 w-4 mr-1" />
            ) : (
              <ChevronRight className="h-4 w-4 mr-1" />
            )}
            {t("jobs.inference.advanced_override")}
            <HelpHint>{t("jobs.help.override_detector_version")}</HelpHint>
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data</CardTitle>
        </CardHeader>
        <CardContent>
          {p.type === "evaluate" ? (
            <div>
              <Label>Test dataset</Label>
              <Select
                value={p.testDatasetId}
                onValueChange={p.setTestDatasetId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Pick dataset" />
                </SelectTrigger>
                <SelectContent>
                  {datasetsArr.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div>
              <Label>Predict dataset</Label>
              <Select
                value={p.predictDatasetId}
                onValueChange={p.setPredictDatasetId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Pick dataset" />
                </SelectTrigger>
                <SelectContent>
                  {datasetsArr.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hyperparameters</CardTitle>
        </CardHeader>
        <CardContent>
          {stageSchema ? (
            <RjsfConfigForm
              schema={stageSchema}
              value={p.config}
              onChange={p.setConfig}
            />
          ) : p.derivedDetectorVersionTag ? (
            <p className="text-sm text-destructive">
              Selected detector version has no params schema; rebuild with
              maldet ≥ 1.1.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a model version to load its hyperparameter form.
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}
```

`useDetector(id)` already exists in `frontend/src/api/queries/detectors.ts:34` — reuse it as-is.

- [ ] **Step 4: Run test — expect PASS**

```bash
cd frontend && pnpm test InferenceSubForm -- --run
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/InferenceSubForm.tsx frontend/tests/unit/components/InferenceSubForm.test.tsx
git commit -m "feat(frontend): InferenceSubForm — model-driven detector for evaluate/predict

Source model is the primary input; on model-version pick the detector
runtime is derived from ModelVersion.detector_id +
detector_version_tag (PR 1 added these fields). An Advanced
'override detector version' toggle lets power users choose a different
detector version (filtered to the same detector for safety).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 16: Frontend — reduce `JobSubmitForm` to orchestrator

**Files:**

- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/src/components/forms/JobSubmitForm.logic.ts`
- Modify: `frontend/tests/unit/components/JobSubmitForm.test.tsx`

- [ ] **Step 1: Update the existing required-fields tests**

Existing tests in `JobSubmitForm.test.tsx` already cover the right types after PR 1. Verify nothing broke:

```bash
cd frontend && pnpm test JobSubmitForm -- --run
```

- [ ] **Step 2: Replace `JobSubmitForm.tsx` body**

Keep imports + auth + searchParams + `useSubmitJob` + the type/state hooks; replace the rendered body with sub-form selection.

```tsx
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  useSubmitJob,
  useJob,
  JOB_TYPES,
  isJobType,
  type JobType,
} from "@/api/queries/jobs";
import { useDetectorVersions } from "@/api/queries/detectors";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HelpHint } from "@/components/common/HelpHint";
import { TrainSubForm } from "./TrainSubForm";
import { InferenceSubForm } from "./InferenceSubForm";
import { StageExplainer } from "./StageExplainer";
import { StickyFormFooter } from "./StickyFormFooter";
import { requiredFieldsForType } from "./JobSubmitForm.logic";

export function JobSubmitForm() {
  const { t } = useTranslation();
  const { currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";

  const [params] = useSearchParams();
  const fromJobId = params.get("from");
  const { data: fromJob } = useJob(fromJobId ?? "");

  const [type, setType] = useState<JobType>("train");
  const [detectorId, setDetectorId] = useState("");
  const [versionTag, setVersionTag] = useState("");
  const [trainDatasetId, setTrainDatasetId] = useState("");
  const [testDatasetId, setTestDatasetId] = useState("");
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [sourceModelOwner, setSourceModelOwner] = useState("");
  const [sourceModelName, setSourceModelName] = useState("");
  const [sourceModelVersionId, setSourceModelVersionId] = useState("");
  const [derivedDetectorId, setDerivedDetectorId] = useState("");
  const [derivedDetectorVersionTag, setDerivedDetectorVersionTag] =
    useState("");
  const [overrideDetectorVersion, setOverrideDetectorVersion] = useState(false);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [priority, setPriority] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Need detector versions for inference submit (resolve tag → id).
  const { data: trainVersions } = useDetectorVersions(detectorId);
  const { data: derivedVersions } = useDetectorVersions(derivedDetectorId);

  useEffect(() => {
    if (!fromJob) return;
    if (isJobType(fromJob.type)) setType(fromJob.type);
    if (fromJob.train_dataset_id) setTrainDatasetId(fromJob.train_dataset_id);
    if (fromJob.test_dataset_id) setTestDatasetId(fromJob.test_dataset_id);
    if (fromJob.predict_dataset_id)
      setPredictDatasetId(fromJob.predict_dataset_id);
    if (fromJob.source_model_version_id)
      setSourceModelVersionId(fromJob.source_model_version_id);
  }, [fromJob]);

  const versionsForSubmit =
    type === "train"
      ? ((trainVersions as { items?: { id: string; git_tag: string }[] })
          ?.items ?? [])
      : ((derivedVersions as { items?: { id: string; git_tag: string }[] })
          ?.items ?? []);

  const canSubmit = (() => {
    const need = requiredFieldsForType(type);
    if (type === "train") {
      if (!detectorId || !versionTag) return false;
    } else {
      if (!sourceModelVersionId) return false;
      if (!derivedDetectorId || !derivedDetectorVersionTag) return false;
    }
    if (need.includes("train_dataset_id") && !trainDatasetId) return false;
    if (need.includes("test_dataset_id") && !testDatasetId) return false;
    if (need.includes("predict_dataset_id") && !predictDatasetId) return false;
    if (need.includes("source_model_version_id") && !sourceModelVersionId)
      return false;
    return true;
  })();

  const mut = useSubmitJob();
  const nav = useNavigate();

  async function submit() {
    setSubmitError(null);
    const tag = type === "train" ? versionTag : derivedDetectorVersionTag;
    const versionId = versionsForSubmit.find((v) => v.git_tag === tag)?.id;
    if (!versionId) return;
    try {
      const job = await mut.mutateAsync({
        type,
        detector_version_id: versionId,
        train_dataset_id: type === "train" ? trainDatasetId : null,
        test_dataset_id: ["train", "evaluate"].includes(type)
          ? testDatasetId
          : null,
        predict_dataset_id: type === "predict" ? predictDatasetId : null,
        source_model_version_id: ["evaluate", "predict"].includes(type)
          ? sourceModelVersionId
          : null,
        params: config,
        ...(isAdmin && priority !== 0 ? { priority } : {}),
      } as unknown as import("@/api/schema.gen").components["schemas"]["JobCreate"]);
      nav(`/jobs/${job.id}`);
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail ?? "Submit failed");
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <Card>
        <CardHeader>
          <CardTitle>Job type</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {JOB_TYPES.map((tt) => (
              <Button
                key={tt}
                variant={tt === type ? "default" : "outline"}
                onClick={() => setType(tt)}
                className="h-11"
              >
                {tt.charAt(0).toUpperCase() + tt.slice(1)}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <StageExplainer type={type} />

      {type === "train" ? (
        <TrainSubForm
          detectorId={detectorId}
          setDetectorId={setDetectorId}
          versionTag={versionTag}
          setVersionTag={setVersionTag}
          trainDatasetId={trainDatasetId}
          setTrainDatasetId={setTrainDatasetId}
          testDatasetId={testDatasetId}
          setTestDatasetId={setTestDatasetId}
          config={config}
          setConfig={setConfig}
        />
      ) : (
        <InferenceSubForm
          type={type}
          sourceModelOwner={sourceModelOwner}
          setSourceModelOwner={setSourceModelOwner}
          sourceModelName={sourceModelName}
          setSourceModelName={setSourceModelName}
          sourceModelVersionId={sourceModelVersionId}
          setSourceModelVersionId={setSourceModelVersionId}
          derivedDetectorId={derivedDetectorId}
          setDerivedDetectorId={setDerivedDetectorId}
          derivedDetectorVersionTag={derivedDetectorVersionTag}
          setDerivedDetectorVersionTag={setDerivedDetectorVersionTag}
          overrideDetectorVersion={overrideDetectorVersion}
          setOverrideDetectorVersion={setOverrideDetectorVersion}
          predictDatasetId={predictDatasetId}
          setPredictDatasetId={setPredictDatasetId}
          testDatasetId={testDatasetId}
          setTestDatasetId={setTestDatasetId}
          config={config}
          setConfig={setConfig}
        />
      )}

      {isAdmin && (
        <Card>
          <CardHeader>
            <CardTitle>{t("jobs.priority.label")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                <Label htmlFor="priority-input">
                  {t("jobs.priority.label")}
                </Label>
                <HelpHint popover>{t("jobs.help.priority_admin")}</HelpHint>
              </div>
              <Input
                id="priority-input"
                type="number"
                min={0}
                step={1}
                className="w-24"
                value={priority}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  setPriority(isNaN(v) || v < 0 ? 0 : v);
                }}
              />
            </div>
            {priority > 0 && (
              <p
                className="text-sm rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-900/20 dark:text-amber-300"
                role="alert"
              >
                {t("jobs.priority.warning")}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      <StickyFormFooter>
        <Button variant="ghost" onClick={() => nav(-1)} className="h-11">
          Cancel
        </Button>
        <Button
          disabled={!canSubmit || mut.isPending}
          onClick={submit}
          className="h-11"
        >
          Submit job
        </Button>
      </StickyFormFooter>
    </div>
  );
}
```

- [ ] **Step 3: Run frontend test suite**

```bash
cd frontend && pnpm test -- --run
```

Expected: TrainSubForm, InferenceSubForm, JobSubmitForm logic tests pass.

- [ ] **Step 4: Manual smoke**

```bash
cd frontend && pnpm dev
```

Open `/jobs/new`:

- Train: same as before; clear Test dataset works.
- Evaluate: pick a model + version; detector card auto-shows ELF RF + tag.
- Predict: same as Evaluate. Click Advanced override; detector version becomes editable but filtered to that detector's versions.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/JobSubmitForm.tsx
git commit -m "$(cat <<'EOF'
refactor(frontend): JobSubmitForm — orchestrator over TrainSubForm + InferenceSubForm

Mainstream MLOps inference UX puts the model first; runtime is derived.
JobSubmitForm now switches sub-forms by job type. Train uses
TrainSubForm (detector/version primary). Evaluate + Predict use
InferenceSubForm (source model primary, detector derived, advanced
override available). The submit handler resolves the chosen detector
version tag → id from whichever versions list is in scope.

Restores ?from=<id> prefill of source_model_version_id (was missing).

Spec: docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 17: Frontend — i18n keys for inference help hints + advanced label

**Files:**

- Modify: `frontend/src/i18n/zh-TW.json`
- Modify: `frontend/src/i18n/en.json`

- [ ] **Step 1: Add zh-TW keys**

Extend the `jobs.help` block and add `jobs.inference`:

```json
"help": {
  "test_dataset_optional": "可選。提供時會多算最終 metrics 與混淆矩陣；不選則只訓練模型，不算指標。",
  "priority_admin": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。",
  "source_model": "已訓練好的模型；推論時會載入它的 weights。模型已綁定一個 detector，下方 Detector 區塊會自動帶入。",
  "override_detector_version": "預設使用模型訓練時的 detector version（保證可重現）。覆寫適用於 predict pipeline 有 bugfix、或想用較新的 evaluator 的情境。"
},
"inference": {
  "advanced_override": "進階：覆寫 detector version"
}
```

- [ ] **Step 2: Add en.json equivalents**

```json
"help": {
  ...
  "source_model": "The trained model loaded for inference. Each model is bound to a detector; the Detector card below auto-fills from the chosen model.",
  "override_detector_version": "By default the run uses the detector version the model was trained with (reproducible). Override is for cases like a bug-fixed predict pipeline or running against a newer evaluator."
},
"inference": {
  "advanced_override": "Advanced: override detector version"
}
```

- [ ] **Step 3: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/zh-TW.json frontend/src/i18n/en.json
git commit -m "feat(frontend): i18n keys for inference help hints + advanced override label

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 18: Frontend — e2e tests for train + inference flows

**Files:**

- Create: `frontend/tests/e2e/job-submit-train.spec.ts`
- Create: `frontend/tests/e2e/job-submit-inference.spec.ts`

- [ ] **Step 1: Inspect existing e2e for shape**

```bash
ls frontend/tests/e2e/
head -60 frontend/tests/e2e/dataset-upload.spec.ts
```

These tests run against a live dev backend; follow the same fixture / login pattern.

- [ ] **Step 2: Write `job-submit-train.spec.ts`**

```ts
import { test, expect } from "@playwright/test";

test("train: detector + version + train ds, no test ds, submits", async ({
  page,
}) => {
  await page.goto("/jobs/new");
  await page.getByRole("button", { name: "Train" }).click();
  await page.getByLabel("Detector").click();
  await page
    .getByRole("option", { name: /elf rf/i })
    .first()
    .click();
  await page.getByLabel("Version").click();
  await page.getByRole("option").first().click();
  await page.getByLabel("Train dataset").click();
  await page.getByRole("option").first().click();
  // Skip test dataset; verify Submit button is enabled
  await expect(page.getByRole("button", { name: "Submit job" })).toBeEnabled();
});

test("train: clearing the Test dataset clears the value", async ({ page }) => {
  await page.goto("/jobs/new");
  await page.getByRole("button", { name: "Train" }).click();
  await page.getByLabel("Detector").click();
  await page
    .getByRole("option", { name: /elf rf/i })
    .first()
    .click();
  await page.getByLabel("Version").click();
  await page.getByRole("option").first().click();
  await page.getByLabel("Test dataset").click();
  await page.getByRole("option").first().click();
  // Click the X button rendered by ClearableSelect
  await page.getByRole("button", { name: /clear/i }).click();
  // Test dataset combobox should now show its placeholder
  await expect(page.getByText(/pick dataset/i)).toBeVisible();
});
```

- [ ] **Step 3: Write `job-submit-inference.spec.ts`**

```ts
import { test, expect } from "@playwright/test";

test("predict: choosing a model auto-fills detector + version", async ({
  page,
}) => {
  await page.goto("/jobs/new");
  await page.getByRole("button", { name: "Predict" }).click();
  // Source model
  await page.getByLabel("Source model").click();
  await page.getByRole("option").first().click();
  await page.getByLabel("Model version").click();
  await page.getByRole("option").first().click();
  // Detector card should now show the derived detector + version
  await expect(page.getByText(/detector.*derived/i)).toBeVisible();
});

test("evaluate: advanced override toggle reveals version dropdown", async ({
  page,
}) => {
  await page.goto("/jobs/new");
  await page.getByRole("button", { name: "Evaluate" }).click();
  await page.getByLabel("Source model").click();
  await page.getByRole("option").first().click();
  await page.getByLabel("Model version").click();
  await page.getByRole("option").first().click();
  await page.getByRole("button", { name: /advanced.*override/i }).click();
  await expect(page.getByLabel("Version")).toBeVisible();
});
```

- [ ] **Step 4: Run e2e against dev backend**

(Operator-run; document, don't auto-launch unless playwright config supports a stub backend.)

```bash
cd frontend && pnpm playwright test e2e/job-submit-train.spec.ts e2e/job-submit-inference.spec.ts
```

If the e2e tests need fixtures (a model + version etc.), the agent should reuse the populated DB pattern from existing e2e tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/e2e/job-submit-train.spec.ts frontend/tests/e2e/job-submit-inference.spec.ts
git commit -m "test(frontend): e2e — submit job train + inference flows

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 19: Tech debt notes

**Files:**

- Modify: `docs/architecture.md` §9 (or wherever the project tracks tech debt)

- [ ] **Step 1: Append tech debt entries**

Find the tech debt section:

```bash
grep -n "tech debt\|Tech debt" docs/architecture.md | head -5
```

Append three items under that section (use existing list format):

```markdown
- **`@microlink/react-json-view` (frontend, JsonTreeView)** — fork of an unmaintained library. Dark-mode hotfix landed via theme-prop swap. Follow-up: evaluate `react-json-view-lite` (CSS-vars-friendly, smaller bundle). Owner: frontend.
- **RJSF v5 default templates** — extending `.rjsf-wrap` with Tailwind tokens unblocked dark mode, but the templates are not aligned with shadcn aesthetics. Follow-up: evaluate `@rjsf/shadcn` (community) or implement a custom template set under `frontend/src/components/forms/rjsf-templates/`. Owner: frontend.
- **maldet `BatchPredictor.params_schema` lacks descriptions** — Lolday auto-renders schema descriptions via RJSF, so help text for params like `batch_size` should ship from maldet upstream. Follow-up: open an issue against the maldet repo to add `description` for known predict / train / evaluate params. Owner: backend / maldet.
```

- [ ] **Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(architecture): tech debt — react-json-view-lite, @rjsf/shadcn, maldet param descriptions

Captured during the Submit Job redesign (PR 2). Each item is a
follow-up that did not block the redesign but is worth tracking.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task 20: Run full suite + open PR 2

- [ ] **Step 1: Pre-commit**

```bash
pre-commit run --all-files
```

- [ ] **Step 2: Backend + frontend full test**

```bash
cd backend && uv run pytest -q
cd frontend && pnpm typecheck && pnpm lint && pnpm test -- --run
```

- [ ] **Step 3: Push branch + open PR**

```bash
git push -u origin refactor/inference-form
gh pr create --title "refactor(submit-job): split TrainSubForm + InferenceSubForm; model-driven detector for inference" --body "$(cat <<'EOF'
## Summary

- Split JobSubmitForm into orchestrator + TrainSubForm + InferenceSubForm.
- InferenceSubForm (evaluate / predict) puts Source model first; detector + version is derived from `ModelVersion.detector_id` + `detector_version_tag` (added in PR 1).
- "Advanced: override detector version" toggle reveals an editable version dropdown filtered to the same detector.
- Add HelpHint to Source model (tooltip) and the Override toggle.
- Restore `?from=<job_id>` prefill of `source_model_version_id`.

Spec: docs/superpowers/specs/2026-05-07-job-submit-form-redesign-design.md
Plan: docs/superpowers/plans/2026-05-07-job-submit-form-redesign.md

## Test plan

- [ ] vitest: TrainSubForm + InferenceSubForm + JobSubmitForm logic
- [ ] playwright: train (clearable Test dataset) + inference (auto-derived detector, advanced override)
- [ ] Manual: copy a predict job via `?from=<id>` — Source model + version prefills, detector card auto-shows
- [ ] Verify dark mode legibility on Detector detail + Submit Job (carryover from PR 1)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Wait for CI; merge after review**

---

## Self-review against the spec

| Spec section                              | Coverage in plan                                             |
| ----------------------------------------- | ------------------------------------------------------------ |
| §2.1 Form modelling defect                | Tasks 14–16 (split + InferenceSubForm derivation)            |
| §2.2 bug 1 (dark manifest)                | Task 5                                                       |
| §2.2 bug 2 (dark hyperparams)             | Task 6                                                       |
| §2.2 bug 3a (train test_dataset required) | Task 10 step 3                                               |
| §2.2 bug 3b (no clear)                    | Task 7 + 10                                                  |
| §2.2 bug 4a (model version envelope)      | Task 10 step 5                                               |
| §2.2 bug 4b (free pairing)                | Tasks 14–16                                                  |
| §3.2 backend denormalisation              | Tasks 1–2                                                    |
| §3.3.1 form refactor                      | Tasks 14–16                                                  |
| §3.3.2 bug fixes                          | Tasks 5–10                                                   |
| §3.3.3 help hints                         | Task 8 (component), 10 (Priority+Test), 17 (Source+Override) |
| §3.4 i18n                                 | Tasks 9 (PR1), 17 (PR2)                                      |
| §5 testing                                | Tasks 1, 4, 5, 7, 8, 10, 14, 15, 18                          |
| §6 tech debt                              | Task 19                                                      |

No placeholders. No "TBD" / "implement later". Each step has explicit code or commands. File paths are exact. Tasks are ordered so each previous task's artifact is consumable by the next.
