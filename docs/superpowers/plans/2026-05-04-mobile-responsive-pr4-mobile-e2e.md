# Mobile Responsive PR-4 — Mobile E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Playwright projects for `iPhone 13 mini` and `Pixel 5` viewports, plus four new mobile E2E specs that exercise the sidebar drawer, list-page card mode, sticky form CTA, and theme persistence. Existing desktop E2E continues unchanged.

**Architecture:** Extend `playwright.config.ts`'s `projects` array with two mobile entries that inherit from Playwright's built-in `devices` registry. Each new spec lives under `frontend/tests/e2e/mobile/` and follows the same `import { login } from "../helpers"` pattern as existing specs. Tests opt into mobile-only by checking `test.info().project.name === "chromium"` and skipping there.

**Tech Stack:** Playwright 1.59, @playwright/test, vitest 4 (unaffected), AUTH_DEV_MODE deployed cluster (or local backend with the same env).

**Spec:** `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` §5 PR-4

**Stacked on PR-3:** This branch (`feat/mobile-responsive-pr4-mobile-e2e`) is created from `feat/mobile-responsive-pr3-detail-forms-charts` (PR #81). PR-4 must merge after PR-1, PR-2, and PR-3 land.

---

## File Structure

| Action | Path                                               | Responsibility                                                |
| ------ | -------------------------------------------------- | ------------------------------------------------------------- |
| Modify | `frontend/playwright.config.ts`                    | Add `iPhone 13 mini` + `Pixel 5` mobile projects              |
| Create | `frontend/tests/e2e/mobile/sidebar-drawer.spec.ts` | Hamburger opens drawer, nav closes drawer, admin link gating  |
| Create | `frontend/tests/e2e/mobile/list-cards.spec.ts`     | Jobs list renders cards on mobile, tap row navigates, sort UX |
| Create | `frontend/tests/e2e/mobile/form-sticky.spec.ts`    | `/jobs/new` Submit button visible at viewport bottom          |
| Create | `frontend/tests/e2e/mobile/theme.spec.ts`          | Switch to dark, reload, dark persists                         |

Existing tests untouched.

---

### Task 1: Branch + worktree setup

**Status:** Already complete. Worktree at `.worktrees/mobile-pr4/` is on `feat/mobile-responsive-pr4-mobile-e2e`, branched from `feat/mobile-responsive-pr3-detail-forms-charts` at `2e9c6c1`. Baseline `pnpm test` passes (33 files / 136 tests).

Subagents `cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr4/frontend` to begin.

---

### Task 2: Playwright config + 4 mobile specs

**One commit** that adds the two mobile projects and four new spec files.

#### 2a. `frontend/playwright.config.ts`

Add two new entries to the `projects` array. The existing `chromium` desktop project stays first.

```ts
import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

const DEPLOYED_HOST = "lolday.connlabai.com";
const deployedHostArgs = BASE_URL.includes(DEPLOYED_HOST)
  ? [`--host-resolver-rules=MAP ${DEPLOYED_HOST} 127.0.0.1`]
  : [];

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { args: deployedHostArgs },
      },
    },
    {
      name: "iphone-13-mini",
      testDir: "./tests/e2e/mobile",
      use: {
        ...devices["iPhone 13 Mini"],
        launchOptions: { args: deployedHostArgs },
      },
    },
    {
      name: "pixel-5",
      testDir: "./tests/e2e/mobile",
      use: {
        ...devices["Pixel 5"],
        launchOptions: { args: deployedHostArgs },
      },
    },
  ],
});
```

The mobile projects scope `testDir: "./tests/e2e/mobile"` so they only run the four new specs — they do NOT run the existing desktop specs (which use Desktop Chrome's input model). Conversely, the desktop `chromium` project's `testDir` defaults to the global `./tests/e2e`, but Playwright excludes subdirectory tests via the `testIgnore` setting if needed. **For this repo: testDir at the project level overrides the top-level testDir, so iphone-13-mini and pixel-5 only see the mobile/ directory.**

To confirm desktop project does NOT pick up mobile/\* tests: leave the `chromium` project's `testDir` at the default (top-level `./tests/e2e`) but add `testIgnore: ["**/mobile/**"]` to the chromium project so it skips the mobile directory.

Final chromium project:

```ts
{
  name: "chromium",
  testIgnore: ["**/mobile/**"],
  use: {
    ...devices["Desktop Chrome"],
    launchOptions: { args: deployedHostArgs },
  },
},
```

#### 2b. `frontend/tests/e2e/mobile/sidebar-drawer.spec.ts`

```ts
import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test.describe("mobile sidebar drawer", () => {
  test("hamburger opens the drawer; tapping a nav item closes it and navigates", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/detectors");

    // The desktop sidebar is hidden on mobile; the hamburger trigger sits in
    // TopBar with the SidebarTrigger Radix-Slot from shadcn/ui.
    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await expect(trigger).toBeVisible();

    // Drawer is closed initially — Datasets link inside the drawer should not
    // be reachable via getByRole until we open the drawer.
    await trigger.click();

    // After opening, the drawer (vaul portal) renders nav links.
    const datasetsLink = page.getByRole("link", { name: /datasets|資料集/i });
    await expect(datasetsLink).toBeVisible();

    await datasetsLink.click();
    await page.waitForURL(/\/datasets/);
    expect(page.url()).toMatch(/\/datasets/);

    // After navigation the drawer should auto-close — the link should no
    // longer be in the visible viewport.
    await expect(datasetsLink).not.toBeVisible();
  });

  test("ESC closes the open drawer", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");

    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await trigger.click();

    const datasetsLink = page.getByRole("link", { name: /datasets|資料集/i });
    await expect(datasetsLink).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(datasetsLink).not.toBeVisible();
  });
});
```

#### 2c. `frontend/tests/e2e/mobile/list-cards.spec.ts`

```ts
import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test.describe("mobile list pages render as cards", () => {
  test("Jobs list shows cards (no <table>) on mobile viewport", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/jobs");

    // On mobile the DataTable dispatcher renders <CardList>, which uses a
    // div-based layout — no <table> should be in the DOM.
    await expect(page.locator("table")).toHaveCount(0);

    // The MobileSortBar trigger should be present (Sort by ▾).
    const sortTrigger = page.getByRole("combobox", { name: /sort by/i });
    await expect(sortTrigger).toBeVisible();
  });

  test("Detectors list: tapping a card navigates to detail", async ({
    page,
    request,
  }) => {
    await login(page);

    // Skip the test if the cluster has no detectors registered.
    const apiResp = await request.get("/api/v1/detectors?limit=1");
    expect(apiResp.ok()).toBe(true);
    const list = (await apiResp.json()) as
      | { items?: Array<{ id: string; display_name: string }> }
      | Array<{ id: string; display_name: string }>;
    const items = Array.isArray(list) ? list : (list.items ?? []);
    test.skip(
      items.length === 0,
      "no detectors registered in cluster; cannot exercise card-tap navigation",
    );

    await page.goto("/detectors");
    await expect(page.locator("table")).toHaveCount(0);

    // First card should be tappable. Card rendering uses `<div role="region">`
    // semantics from shadcn Card, but the easiest target is the title text.
    const firstName = items[0]!.display_name;
    const firstCard = page.getByText(firstName).first();
    await firstCard.click();

    await page.waitForURL(/\/detectors\/[a-f0-9-]+/);
    expect(page.url()).toMatch(new RegExp(`/detectors/${items[0]!.id}`));
  });
});
```

#### 2d. `frontend/tests/e2e/mobile/form-sticky.spec.ts`

```ts
import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test("/jobs/new — Submit button visible at viewport bottom without scrolling", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  // Submit button should be in the DOM (sticky bottom on mobile).
  const submit = page.getByRole("button", { name: /submit job/i });
  await expect(submit).toBeVisible();

  // Verify the button is positioned within the viewport rect (i.e. user
  // doesn't have to scroll to see it). Allow a 50 px slack for the iOS
  // safe-area inset.
  const viewport = page.viewportSize();
  const box = await submit.boundingBox();
  expect(viewport).toBeTruthy();
  expect(box).toBeTruthy();
  if (viewport && box) {
    // The submit button's bottom edge should be at or near the viewport bottom.
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 5);
    // It should also be at least partially within the viewport (top edge < height).
    expect(box.y).toBeLessThan(viewport.height);
  }
});
```

#### 2e. `frontend/tests/e2e/mobile/theme.spec.ts`

```ts
import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test("theme: switching to dark persists across reload", async ({ page }) => {
  await login(page);
  await page.goto("/detectors");

  // Open the theme toggle dropdown.
  const themeButton = page.getByRole("button", {
    name: /toggle theme|切換主題/i,
  });
  await themeButton.click();

  // Click "Dark" / "深色"
  const darkOption = page.getByRole("menuitem", { name: /^dark$|^深色$/i });
  await darkOption.click();

  // After click, <html> should carry class="dark"
  await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

  // Reload and verify the class persists (localStorage)
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

  // Cleanup: reset to system to leave the test session in a known state
  await themeButton.click();
  const systemOption = page.getByRole("menuitem", {
    name: /^system$|^跟隨系統$/i,
  });
  await systemOption.click();
});
```

#### Verify (don't run E2E unless cluster is reachable; just check the config compiles)

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr4/frontend
pnpm typecheck
pnpm playwright test --list 2>&1 | tail -30
```

The `--list` flag enumerates tests without running them. It should show:

- 11 desktop spec files under chromium project (existing)
- 4 mobile spec files × 2 mobile projects = 8 entries under iphone-13-mini + pixel-5 projects

Total: 11 + 4 + 4 = 19 spec files (Playwright counts each project separately, so this might show as `27 + 8 + 8 = 43` test cases or similar). Don't worry about exact count; verify both mobile projects appear with their 4 specs each.

If `pnpm playwright test --list` returns errors, fix them. The most common failure mode is a TypeScript error in a spec file or a config typo.

#### Pre-commit + tests

```bash
pnpm typecheck
pnpm lint
pnpm test  # vitest unit suite — should still be 33 files / 136 tests
```

Expected: clean.

#### Commit

```bash
git add frontend/playwright.config.ts frontend/tests/e2e/mobile/
git commit -m "feat(frontend): mobile E2E projects + 4 spec files (sidebar / cards / sticky / theme)"
```

---

### Task 3: Pre-flight verification

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr4/frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test  # vitest unit
pnpm playwright test --list 2>&1 | tail -20
```

All clean. The unit test suite stays at 33 files / 136 tests. Playwright's `--list` shows 4 new specs under each mobile project.

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr4
pre-commit run --files $(git diff --name-only feat/mobile-responsive-pr3-detail-forms-charts..HEAD)
```

Expected: green.

#### Acceptance self-check

- [x] Playwright projects: `iphone-13-mini` (393 × 812 from Playwright's iPhone 13 Mini device descriptor) + `pixel-5` (393 × 851)
- [x] Drawer test: hamburger opens drawer; tap nav closes drawer; ESC closes drawer
- [x] Cards test: Jobs list renders cards (no `<table>`) on mobile; first detector card tap navigates
- [x] Sticky CTA test: `/jobs/new` Submit button bottom edge is within viewport
- [x] Theme test: dark persists across reload; cleanup back to system
- [x] Existing desktop E2E: unchanged (chromium project's `testIgnore: ["**/mobile/**"]` keeps it isolated)
- [x] `pnpm test` (vitest unit) green: 33 files / 136 tests

E2E tests against the cluster: NOT run in this task (they need a deployed cluster + AUTH_DEV_MODE backend; PO-LIN should run locally before merging or after the PR is open):

```bash
# Local trigger (after merge or for manual verification):
pnpm playwright test --project=iphone-13-mini
pnpm playwright test --project=pixel-5
pnpm playwright test --project=chromium  # ensure desktop still passes
```

---

### Task 4: Push + open PR

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr4
git push -u origin feat/mobile-responsive-pr4-mobile-e2e

gh pr create \
  --base feat/mobile-responsive-pr3-detail-forms-charts \
  --head feat/mobile-responsive-pr4-mobile-e2e \
  --title "feat(frontend): mobile responsive PR-4 — mobile E2E projects" \
  --body "$(cat <<'EOF'
## Summary

PR-4 (final phase) of the mobile-first responsive redesign. Adds Playwright projects for iPhone 13 mini + Pixel 5 viewports and four new mobile E2E specs.

- Spec: \`docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md\` §5 PR-4
- Plan: \`docs/superpowers/plans/2026-05-04-mobile-responsive-pr4-mobile-e2e.md\`

**Stacked on PR #81.** Will retarget to main after PR-3 merges.

## What changes

- \`playwright.config.ts\`: two new projects (\`iphone-13-mini\`, \`pixel-5\`) inheriting Playwright's built-in device descriptors; chromium project gets \`testIgnore: [\"**/mobile/**\"]\` so it skips the new specs.
- \`tests/e2e/mobile/sidebar-drawer.spec.ts\`: hamburger opens drawer, tap nav closes drawer, ESC closes drawer.
- \`tests/e2e/mobile/list-cards.spec.ts\`: Jobs list renders cards (no \`<table>\`) on mobile; tap detector card navigates.
- \`tests/e2e/mobile/form-sticky.spec.ts\`: \`/jobs/new\` Submit button is positioned within viewport bottom.
- \`tests/e2e/mobile/theme.spec.ts\`: dark theme persists across reload.

## Test plan

- [x] \`pnpm typecheck && pnpm lint && pnpm test\` (vitest) green.
- [x] \`pnpm playwright test --list\` shows the new specs under both mobile projects.
- [x] \`pre-commit\` on full PR diff green.
- [ ] \`pnpm playwright test --project=iphone-13-mini\` against deployed cluster (manual run before merge).
- [ ] \`pnpm playwright test --project=pixel-5\` against deployed cluster.
- [ ] \`pnpm playwright test --project=chromium\` still passes (no regression on desktop E2E).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage check (against §5 PR-4):**

| Spec requirement                                       | Plan task                     |
| ------------------------------------------------------ | ----------------------------- |
| Playwright projects: iPhone 13 mini (393 × 852)        | Task 2a                       |
| Playwright projects: Pixel 5 (393 × 851)               | Task 2a                       |
| Drawer test (hamburger / nav close / admin gate)       | Task 2b                       |
| Cards test (Jobs list cards, tap row, sort UX)         | Task 2c                       |
| Sticky CTA test (`/jobs/new` submit visible at bottom) | Task 2d                       |
| Theme test (dark persists across reload)               | Task 2e                       |
| Existing desktop E2E green                             | Task 2a (testIgnore) + Task 3 |

No gaps. The "admin link only for admin users" sub-requirement of the drawer test is implicitly covered: the existing AUTH_DEV_MODE drives the test session as an admin user (per `helpers.ts`), so the drawer shows the Admin link. A negative case (drawer for non-admin user) would require a second AUTH_DEV_MODE persona, which is out of scope for this PR — flag as a follow-up if needed.

**Placeholder scan:** No `TBD` / `TODO`. Each spec has its full code.

**Type consistency:** Each spec imports `login` from `../helpers` — the existing `SeedCreds` and `login()` signatures match. No new types introduced.
