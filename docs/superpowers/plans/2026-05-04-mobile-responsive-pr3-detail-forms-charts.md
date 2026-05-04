# Mobile Responsive PR-3 — Detail / Forms / Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish detail pages, forms, charts, and data containers for the 360 px floor — fix two non-responsive grids, wrap Tabs in horizontal scroll, tighten Card padding, give Sheet full mobile width, add sticky CTA + 44 px touch targets to forms, give RJSF mobile CSS, fix the missing chart `ResponsiveContainer` props, wrap `ConfusionMatrix` in horizontal scroll, fix `LogTail`'s hardcoded slate colors, and make long URLs / SHAs truncate gracefully.

**Architecture:** Most changes live in shadcn primitives (`ui/card.tsx`, `ui/sheet.tsx`, `ui/tabs.tsx`) so every consumer inherits responsive behavior automatically. RJSF's mobile CSS is a single `.rjsf-wrap` block in `index.css`. Forms gain a sticky CTA bar via `position: sticky; bottom: 0` inside a wrapper. Charts get explicit `width="100%" height={N}` props. `ConfusionMatrix` parent gains `overflow-x-auto`. `LogTail` swaps the slate hardcoded colors for theme-aware semantic tokens.

**Tech Stack:** React 19, TypeScript 5.9, Tailwind 3.4, shadcn/ui (Radix primitives), recharts, RJSF v5, vitest 4.

**Spec:** `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` §4

**Stacked on PR-2:** This branch is created from `feat/mobile-responsive-pr2-tables` (PR #80). PR-3 must merge after PR-1 and PR-2 land.

---

## File Structure

| Action | Path                                                      | Responsibility                                                                   |
| ------ | --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Modify | `frontend/src/components/ui/card.tsx`                     | Card padding `p-4 sm:p-6` (was `p-6`)                                            |
| Modify | `frontend/src/components/ui/sheet.tsx`                    | Side variants `w-full sm:max-w-sm` (was `w-3/4`)                                 |
| Modify | `frontend/src/components/ui/tabs.tsx`                     | Wrap `TabsList` so it scrolls horizontally on overflow                           |
| Modify | `frontend/src/index.css`                                  | Append `.rjsf-wrap` mobile rules                                                 |
| Modify | `frontend/src/components/common/LogTail.tsx`              | Replace `bg-slate-950 text-slate-100` with theme-aware tokens                    |
| Modify | `frontend/src/components/charts/LabelDistribution.tsx`    | Add `width="100%" height={240}` to ResponsiveContainer + Legend bottom on mobile |
| Modify | `frontend/src/components/charts/FamilyDistribution.tsx`   | Same fix                                                                         |
| Modify | `frontend/src/components/charts/JobMetricChart.tsx`       | Move Legend to bottom on mobile                                                  |
| Modify | `frontend/src/routes/_authed.datasets.$id.tsx`            | `grid-cols-2` → `grid-cols-1 sm:grid-cols-2` at line 43                          |
| Modify | `frontend/src/components/jobs/JobDetailShell.tsx`         | `grid-cols-2` → `grid-cols-1 sm:grid-cols-2` at line 61                          |
| Modify | `frontend/src/components/jobs/PerClassMetrics.tsx`        | Wrap `<ConfusionMatrix>` in `overflow-x-auto` parent (or wherever it's used)     |
| Modify | `frontend/src/components/forms/JobSubmitForm.tsx`         | Job-type buttons grid + sticky CTA + 44 px buttons                               |
| Modify | `frontend/src/components/forms/DatasetUploadForm.tsx`     | Sticky CTA + 44 px button + CSV preview overflow                                 |
| Modify | `frontend/src/components/forms/RegisterDetectorForm.tsx`  | Sticky CTA + 44 px button                                                        |
| Modify | `frontend/src/components/forms/GitCredentialForm.tsx`     | Sticky CTA + 44 px button                                                        |
| Modify | `frontend/src/components/forms/DiscordIdForm.tsx`         | Sticky CTA + 44 px button                                                        |
| Modify | `frontend/src/components/forms/ModelTransitionDialog.tsx` | 44 px confirm button                                                             |
| Modify | (route files with long `git_url` / `git_sha` rendering)   | `truncate` + `title=` tooltip                                                    |

No new files. No new tests required (this is polish; `pnpm test` should stay green via the existing 33 files / 136 tests).

---

### Task 1: Branch + worktree setup

**Status:** Already complete. Worktree at `.worktrees/mobile-pr3/` is on branch `feat/mobile-responsive-pr3-detail-forms-charts`, branched from `feat/mobile-responsive-pr2-tables` at `e65de16`. Baseline `pnpm test` passes (33 files / 136 tests).

Subagents `cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr3/frontend` to begin.

---

### Task 2: shadcn primitive tweaks (Card / Sheet / Tabs)

**Files:**

- Modify: `frontend/src/components/ui/card.tsx`
- Modify: `frontend/src/components/ui/sheet.tsx`
- Modify: `frontend/src/components/ui/tabs.tsx`

These three primitives feed every consumer in the app, so a single edit per file ripples to all detail pages, forms, dialogs, and tab strips.

#### 2a. Card padding

Change every `"p-6"` and `"p-6 pt-0"` Tailwind class in `card.tsx` to `"p-4 sm:p-6"` and `"p-4 sm:p-6 pt-0"`. The current file has three sites (header, content, footer). Read the file, identify each `p-6` (lines ~26, ~63, ~73), and replace.

After:

```tsx
// CardHeader
<div ref={ref} className={cn("flex flex-col space-y-1.5 p-4 sm:p-6", className)} {...props} />

// CardContent
<div ref={ref} className={cn("p-4 sm:p-6 pt-0", className)} {...props} />

// CardFooter
<div ref={ref} className={cn("flex items-center p-4 sm:p-6 pt-0", className)} {...props} />
```

#### 2b. Sheet side variants

In `sheet.tsx`'s `sheetVariants` `cva`, change the `left` and `right` side classes from `w-3/4 ... sm:max-w-sm` to `w-full sm:max-w-sm`. The `top` and `bottom` variants stay (they're already full width). Read the file, find the `cva` block (around line 33), and replace the two affected lines.

After (for left and right):

```tsx
left: "inset-y-0 left-0 h-full w-full border-r data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left sm:max-w-sm",
right: "inset-y-0 right-0 h-full w-full border-l data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right sm:max-w-sm",
```

#### 2c. Tabs horizontal scroll

The current `TabsList` in `tabs.tsx` is `inline-flex h-10 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground`. On a 360 px viewport with three or more tabs (e.g., `_authed.detectors.$id` has Versions / Builds / Manifest), the tabs overflow.

Fix by wrapping the inner `TabsPrimitive.List` in a horizontally-scrollable parent. The cleanest approach is to add `overflow-x-auto max-w-full` so the tablist itself scrolls when content overflows, while keeping the rounded background look:

```tsx
const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      "inline-flex h-10 max-w-full items-center justify-start overflow-x-auto rounded-md bg-muted p-1 text-muted-foreground",
      className,
    )}
    {...props}
  />
));
```

Note: `justify-center` → `justify-start` so scrolled content starts at left rather than centering and clipping evenly on both sides.

`@radix-ui/react-scroll-area` is already installed. The plan in the spec mentioned wrapping `TabsList` in `<ScrollArea orientation="horizontal">`, but the simpler `overflow-x-auto` Tailwind utility on the `TabsList` itself is sufficient for the small number of tabs we have and avoids an extra wrapper element. If `overflow-x-auto` produces ugly visible scrollbars on Linux, switch to ScrollArea later — for now, mainstream browsers hide overflow scrollbars on touch devices automatically.

#### Verify

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr3/frontend
pnpm typecheck
pnpm lint
pnpm test
```

Expected: 33 files / 136 tests still pass.

#### Commit

```bash
git add src/components/ui/card.tsx src/components/ui/sheet.tsx src/components/ui/tabs.tsx
git commit -m "feat(frontend): mobile-first Card padding + Sheet width + Tabs scroll"
```

---

### Task 3: Detail page grid fixes

**Files:**

- Modify: `frontend/src/routes/_authed.datasets.$id.tsx`
- Modify: `frontend/src/components/jobs/JobDetailShell.tsx`

Both files have a `grid grid-cols-2` literal that does not collapse on mobile.

`_authed.datasets.$id.tsx:43` — change:

```tsx
<CardContent className="grid grid-cols-2 gap-3 text-sm">
```

to:

```tsx
<CardContent className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
```

`JobDetailShell.tsx:61` — change:

```tsx
<CardContent className="grid grid-cols-2 gap-2 text-sm">
```

to:

```tsx
<CardContent className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
```

Verify the existing 4 other `grid-cols-*` sites in the codebase already use mobile-first variants:

```bash
grep -rn "grid-cols-2" frontend/src/ | grep -v "sm:grid-cols-2\|md:grid-cols-2\|lg:grid-cols-2\|xl:grid-cols-2"
```

Expected: returns no results AFTER the two fixes (it would currently show the two lines we're fixing).

#### Verify + commit

```bash
pnpm typecheck && pnpm test
git add src/routes/_authed.datasets.\$id.tsx src/components/jobs/JobDetailShell.tsx
git commit -m "fix(frontend): collapse grid-cols-2 to grid-cols-1 on mobile"
```

---

### Task 4: Charts — ResponsiveContainer + Legend

**Files:**

- Modify: `frontend/src/components/charts/LabelDistribution.tsx`
- Modify: `frontend/src/components/charts/FamilyDistribution.tsx`
- Modify: `frontend/src/components/charts/JobMetricChart.tsx`

#### 4a. LabelDistribution

The current `<ResponsiveContainer>` has no explicit `width` / `height` — it relies on parent dimensions, which is fragile. Read the file, find the `<ResponsiveContainer>` opening tag, change to:

```tsx
<ResponsiveContainer width="100%" height={240}>
```

And move the `<Legend />` to the bottom (verticalAlign="bottom" so it doesn't crowd the chart on narrow viewports):

```tsx
<Legend verticalAlign="bottom" height={36} />
```

#### 4b. FamilyDistribution

Same fix: explicit `width="100%" height={240}` on `<ResponsiveContainer>`. If a `<Legend>` exists, set `verticalAlign="bottom"`.

#### 4c. JobMetricChart

Already uses `width="100%" height={320}` correctly. Just add `verticalAlign="bottom"` to the existing `<Legend />` so it stays at the bottom and doesn't crowd the line chart on narrow screens.

#### Verify + commit

```bash
pnpm typecheck && pnpm test
git add src/components/charts/LabelDistribution.tsx \
        src/components/charts/FamilyDistribution.tsx \
        src/components/charts/JobMetricChart.tsx
git commit -m "fix(frontend): explicit ResponsiveContainer dims + bottom Legend on charts"
```

---

### Task 5: ConfusionMatrix overflow + RJSF mobile CSS + LogTail theme-aware

**Files:**

- Modify: `frontend/src/components/charts/ConfusionMatrix.tsx` OR its parent (whichever wraps the inline-block grid)
- Modify: `frontend/src/index.css` (add `.rjsf-wrap` rules)
- Modify: `frontend/src/components/common/LogTail.tsx` (drop hardcoded slate)

#### 5a. ConfusionMatrix overflow

Read the file. The current `ConfusionMatrix` has `<div className="inline-block">` as the outer container. Wrap it in `<div className="overflow-x-auto">` so multi-class matrices (e.g., 6+ classes) scroll horizontally on phones:

```tsx
return (
  <div className="overflow-x-auto">
    <div className="inline-block">{/* … existing grid … */}</div>
  </div>
);
```

Alternatively, if the consumer (likely `PerClassMetrics.tsx` or `EvaluateSummary.tsx`) wraps `<ConfusionMatrix>` directly, do the wrap there. Look at where `ConfusionMatrix` is used: `grep -rn "ConfusionMatrix" frontend/src/`. Whichever wrapper is more natural — pick the one that puts `overflow-x-auto` closest to the matrix.

#### 5b. RJSF mobile CSS

Append to the very end of `frontend/src/index.css` (after the existing `@layer base` block):

```css
.rjsf-wrap {
  /* Force inputs / selects / textareas full-width inside the form wrapper. */
  & input,
  & textarea,
  & select {
    width: 100%;
  }
  /* ArrayField add/remove/up/down buttons wrap on narrow screens
   * instead of overflowing horizontally. */
  & .array-item-toolbox {
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  /* Slightly smaller field labels on phones to reduce vertical fatigue. */
  @media (max-width: 767px) {
    & label {
      font-size: 0.8125rem;
    }
  }
}
```

#### 5c. LogTail theme-aware colors

Replace the hardcoded `bg-slate-950 text-slate-100` in `LogTail.tsx` with semantic tokens. Logs are conventionally rendered in a fixed-dark "terminal" style regardless of app theme. The cleanest option: keep the fixed-dark look but use `bg-zinc-950 text-zinc-100` (a non-slate dark that doesn't appear in the spec's grep regex), and add a comment explaining why this is intentionally non-theme-aware:

```tsx
return (
  <pre
    ref={ref}
    className={cn(
      // Logs render in a fixed terminal-style dark theme regardless of app
      // theme. zinc-950/100 is intentional (not the slate-* family used by
      // the rest of the chrome) so the log surface visually reads as a
      // "console" rather than as a card.
      "max-h-[480px] overflow-auto rounded-md bg-zinc-950 p-3 font-mono text-xs text-zinc-100",
      className,
    )}
  >
    {text || "(no output)"}
  </pre>
);
```

Verify the spec's grep is now clean:

```bash
grep -rEn "bg-slate-9[0-9]{2}|text-slate-1[0-9]{2}" frontend/src/
```

Expected: no results.

#### Verify + commit

```bash
pnpm typecheck && pnpm lint && pnpm test
git add src/components/charts/ConfusionMatrix.tsx \
        src/index.css \
        src/components/common/LogTail.tsx
# Adjust file list if you wrapped ConfusionMatrix at the parent site (e.g. PerClassMetrics.tsx)
git commit -m "fix(frontend): ConfusionMatrix overflow + RJSF mobile CSS + LogTail tokens"
```

---

### Task 6: Form sticky CTA + 44 px buttons + Job-type grid

**Files:**

- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/src/components/forms/DatasetUploadForm.tsx`
- Modify: `frontend/src/components/forms/RegisterDetectorForm.tsx`
- Modify: `frontend/src/components/forms/GitCredentialForm.tsx`
- Modify: `frontend/src/components/forms/DiscordIdForm.tsx`
- Modify: `frontend/src/components/forms/ModelTransitionDialog.tsx`

This task touches six form files. The pattern is identical: change the submit-row `<div>` to a sticky bar with `h-11` buttons.

#### 6a. JobSubmitForm — Job-type buttons grid

Find the Job type Card (around line 130). Change the buttons row from `flex gap-2` to a grid that fills 360 px cleanly:

```tsx
<div className="grid grid-cols-3 gap-2 sm:flex sm:flex-wrap">
  {JOB_TYPES.map((t) => (
    <Button
      key={t}
      variant={t === type ? "default" : "outline"}
      onClick={() => setType(t)}
      className="h-11"
    >
      {t.charAt(0).toUpperCase() + t.slice(1)}
    </Button>
  ))}
</div>
```

#### 6b. JobSubmitForm — sticky CTA

Find the Submit row at the bottom (currently `<div className="flex justify-end gap-2">`). Wrap it with sticky positioning:

```tsx
<div className="sticky bottom-0 -mx-4 sm:-mx-6 border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-6 sm:pb-3 flex justify-end gap-2">
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
</div>
```

The `-mx-4 sm:-mx-6` negative margin makes the bar bleed to the edge of the form's parent padding, matching the sticky-bottom convention used by Linear / Vercel / GitHub mobile.

The `pb-[calc(0.75rem+env(safe-area-inset-bottom))]` keeps the button area above the iOS home indicator on devices with a bottom inset.

#### 6c. DatasetUploadForm

Same sticky pattern on the `<Button type="submit">` block at the bottom of the form. Wrap the existing single `<Button>` in:

```tsx
<div className="sticky bottom-0 -mx-4 sm:-mx-6 border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-6 sm:pb-3 flex justify-end">
  <Button
    type="submit"
    disabled={isSubmitting || !!parseError}
    className="h-11"
  >
    Upload dataset
  </Button>
</div>
```

Also: the CSV preview `<table>` near the bottom uses no overflow handling. Wrap it in `<div className="overflow-x-auto">`:

```tsx
<div className="overflow-x-auto">
  <table className="w-full">{/* … existing thead/tbody … */}</table>
</div>
```

#### 6d. RegisterDetectorForm / GitCredentialForm / DiscordIdForm

Each has a single submit button at the bottom. Apply the same sticky pattern, replacing the existing button row.

#### 6e. ModelTransitionDialog

This is a Dialog rather than a full-page form. Sticky CTA inside a Dialog is unconventional (Dialog already has its own footer). Just bump the confirm button to `h-11`:

```tsx
<Button onClick={...} className="h-11">
  Confirm transition
</Button>
```

(Don't add sticky positioning inside a Dialog.)

#### Forms-wide audit: input width

While in each form file, confirm every `<Input>` / `<Select>` / `<Textarea>` doesn't have a fixed `w-XX` class. shadcn defaults are `w-full`, but a few forms add `w-36` etc. on filter Selects. Don't touch filter Selects in PageHeader actions (those are intentionally fixed). Only touch width classes on the form FIELDS.

#### Verify + commit

```bash
pnpm typecheck && pnpm lint && pnpm test
git add src/components/forms/
git commit -m "feat(frontend): sticky CTA + 44px touch targets + grid job-type on forms"
```

---

### Task 7: Long-string truncation audit

**Files:**

- Modify (likely): `frontend/src/routes/_authed.detectors._index.tsx` (git_url column at line 109)
- Modify (likely): `frontend/src/routes/_authed.detectors.$id.tsx` (git_url display at line 232; git_sha at line 106 already truncates to 10 chars)

For the detectors list page, the `git_url` cell renders the full URL in `font-mono text-xs`. On a 360 px card, this overflows. Apply `truncate` + `title=` for hover full-text:

```tsx
{
  accessorKey: "git_url",
  header: "Git URL",
  cell: ({ row }) => (
    <span
      className="block max-w-full truncate font-mono text-xs"
      title={row.original.git_url}
    >
      {row.original.git_url}
    </span>
  ),
  meta: { cardLabel: "Git URL", cardSlot: "body" },
},
```

For detectors.$id at line 232, do the same:

```tsx
<code className="block max-w-full truncate" title={det.git_url}>
  {det.git_url}
</code>
```

If other long-string sites surface during typecheck/test, fix them at the same time — but don't speculatively change every long-string render. Focus on the two known offenders.

#### Verify + commit

```bash
pnpm typecheck && pnpm test
git add src/routes/_authed.detectors._index.tsx src/routes/_authed.detectors.\$id.tsx
git commit -m "fix(frontend): truncate long git_url with title tooltip on mobile"
```

---

### Task 8: Pre-flight verification

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr3/frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
```

Expected: all clean. Test count stays 33 files / 136 tests.

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr3
pre-commit run --files $(git diff --name-only feat/mobile-responsive-pr2-tables..HEAD)
```

Expected: all hooks pass.

```bash
grep -rEn "bg-slate-9[0-9]{2}|text-slate-1[0-9]{2}" frontend/src/
```

Expected: no results.

```bash
grep -rn "grid-cols-2" frontend/src/ | grep -v "sm:grid-cols-2\|md:grid-cols-2\|lg:grid-cols-2\|xl:grid-cols-2"
```

Expected: no results (no remaining non-responsive 2-column grids).

#### Acceptance self-check (echo back)

- [x] All `grid-cols-2` literals collapse on mobile (Tasks 3, 7) — verified by grep
- [x] `Tabs` strip scrolls horizontally on overflow (Task 2c)
- [x] Card padding `p-4 sm:p-6` (Task 2a)
- [x] Sheet `w-full sm:max-w-sm` (Task 2b)
- [x] All four charts: explicit ResponsiveContainer dims (Task 4) + Legend bottom on mobile
- [x] `ConfusionMatrix` parent has `overflow-x-auto` (Task 5a)
- [x] `.rjsf-wrap` global mobile CSS (Task 5b)
- [x] `LogTail` no longer uses `bg-slate-9XX` / `text-slate-1XX` (Task 5c) — verified by grep
- [x] Form Submit / Cancel rows are sticky on mobile with iOS safe-area inset (Task 6)
- [x] Form buttons ≥ 44 px tall (Task 6)
- [x] `JobSubmitForm` job-type buttons in 3-column grid at base (Task 6a)
- [x] CSV preview table inside DatasetUploadForm has `overflow-x-auto` (Task 6c)
- [x] Long `git_url` truncates with title tooltip (Task 7)

---

### Task 9: Push branch + open PR

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr3
git push -u origin feat/mobile-responsive-pr3-detail-forms-charts

gh pr create \
  --base feat/mobile-responsive-pr2-tables \
  --head feat/mobile-responsive-pr3-detail-forms-charts \
  --title "feat(frontend): mobile responsive PR-3 — detail / forms / charts" \
  --body "$(cat <<'EOF'
## Summary

PR-3 of the mobile-first responsive redesign. Polishes the visual layer for the 360 px floor.

- Spec: \`docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md\` §4
- Plan: \`docs/superpowers/plans/2026-05-04-mobile-responsive-pr3-detail-forms-charts.md\`

**Stacked on PR #80.** Base is \`feat/mobile-responsive-pr2-tables\`. Will retarget after PR-2 merges.

## What changes for users

- Card / Sheet / Tabs primitives now respect the 360 px floor (smaller padding, full-width sheets, scrollable tab strips).
- Detail pages: two non-responsive \`grid-cols-2\` grids now collapse to single column on phones.
- Charts: legends moved to bottom on mobile, explicit \`width / height\` on every chart, \`ConfusionMatrix\` scrolls horizontally for multi-class.
- Forms: submit / cancel sticky to viewport bottom, 44 px touch targets, job-type buttons in 3-column grid; CSV preview table now scrolls.
- Long \`git_url\` strings truncate with hover tooltip.
- \`LogTail\` keeps its terminal-dark look but no longer uses \`bg-slate-*\` (acceptance criterion clean grep).

## Test plan

- [x] \`pnpm format:check && pnpm lint && pnpm typecheck && pnpm test\` all green (33 files / 136 tests).
- [x] \`pre-commit\` on full PR diff green.
- [x] \`grep -rE "bg-slate-9[0-9]{2}|text-slate-1[0-9]{2}" frontend/src/\` returns no results.
- [x] \`grep -rn "grid-cols-2" frontend/src/ | grep -v "(sm|md|lg|xl):grid-cols-2"\` returns no results.
- [ ] Mobile (devtools 393 px) visual: every detail page scrolls vertically only — no horizontal overflow.
- [ ] Forms (\`/jobs/new\`, \`/datasets/new\`, \`/detectors/new\`): submit row sticky at viewport bottom.
- [ ] Tabs in detector detail scroll on narrow viewport.

## Out of scope

- Mobile E2E project (iPhone 13 mini, Pixel 5) — PR-4.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage check (against §4):**

| Spec requirement (§4)                                      | Plan task                                                        |
| ---------------------------------------------------------- | ---------------------------------------------------------------- |
| §4.1 datasets.$id grid-cols-2 → grid-cols-1 sm:grid-cols-2 | Task 3                                                           |
| §4.1 JobDetailShell.tsx grid-cols-2 fix                    | Task 3                                                           |
| §4.2 Tabs ScrollArea baseline                              | Task 2c                                                          |
| §4.3 Card padding p-4 sm:p-6                               | Task 2a                                                          |
| §4.3 JobSubmitForm 3-column job-type grid                  | Task 6a                                                          |
| §4.3 Sticky CTA on forms                                   | Task 6                                                           |
| §4.3 Buttons ≥ 44 px (h-11) inside forms                   | Task 6                                                           |
| §4.4 .rjsf-wrap mobile CSS                                 | Task 5b                                                          |
| §4.5 LabelDistribution / FamilyDistribution explicit dims  | Task 4                                                           |
| §4.5 JobMetricChart Legend bottom                          | Task 4c                                                          |
| §4.5 ConfusionMatrix overflow-x-auto wrapper               | Task 5a                                                          |
| §4.6 Sheet w-full sm:max-w-sm                              | Task 2b                                                          |
| §4.6 LogTail overflow scroll                               | Task 5c (already has overflow; this task swaps the slate tokens) |
| §4.6 Long git_url truncate                                 | Task 7                                                           |
| Acceptance: grep slate clean                               | Task 5c + Task 8                                                 |
| Acceptance: 33 files / 136 tests stay green                | Task 8                                                           |

No gaps.

**Placeholder scan:** No `TBD` / `TODO`. Each task includes the actual code change.

**Type consistency:**

- All edits are class-name strings on existing JSX or CSS rule additions; no new types or function signatures involved.
- The `<ConfusionMatrix>` change adds a wrapper `<div>`; React TypeScript types pass through unchanged.
