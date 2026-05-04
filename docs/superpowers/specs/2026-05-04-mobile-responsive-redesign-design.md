# Mobile-First Responsive Redesign — Design Specification

> Date: 2026-05-04
> Owner: PO-LIN LAI
> Status: design approved (brainstorm), pending implementation plan

## Overview

Lolday's frontend works on desktop but fails on phones. The sidebar pins 240 px out of every 360–414 px viewport, every list page shows a 4–5 column table that overflows, forms render with desktop padding, and the `Sidebar.tsx` component hardcodes `bg-slate-900` so it ignores theme switching. PO-LIN runs full operations from a phone — submitting jobs, registering detectors, uploading datasets — so monitor-only fixes will not do.

This spec replaces the layout shell, sidebar, and `DataTable` with mainstream responsive patterns, and adds a light/dark theme system. It applies mobile-first Tailwind conventions throughout. The breakpoint floor is 360 px (modern Android budget devices). The desktop experience gains a collapsible sidebar but otherwise stays close to today's layout.

**Design principles:**

- Mobile-first: design at the `base` breakpoint, opt up with `sm:` / `md:` / `lg:`.
- Mainstream over clever: shadcn/ui Sidebar block, TanStack Table column meta, recharts `ResponsiveContainer`, official shadcn Vite ThemeProvider.
- Root-cause fixes only: replace `Sidebar.tsx`, refactor `DataTable.tsx`, do not paper over with parallel mobile components.
- Breaking changes accepted: backward compatibility is not a goal.

---

## 1. Decisions Locked During Brainstorm

| Decision              | Choice                                                                                           |
| --------------------- | ------------------------------------------------------------------------------------------------ |
| Mobile scope          | Full operations on phone (forms + state changes), not monitor-only                               |
| Sidebar pattern       | shadcn/ui Sidebar block — mobile drawer + desktop collapsible icon-only mode                     |
| Sidebar icons         | Boxes / Database / Play / FlaskConical / Layers / UserCog (User + LogOut kept)                   |
| Table on mobile       | Row-to-card transformation; desktop keeps `<Table>`                                              |
| Theme                 | Light / Dark / System toggle, shadcn Vite `ThemeProvider`, persisted in `localStorage`           |
| Mobile floor          | 360 px                                                                                           |
| Tailwind breakpoints  | Defaults — sm 640, md 768, lg 1024, xl 1280                                                      |
| Mobile/desktop split  | 768 px (`md:`), aligning with shadcn Sidebar block                                               |
| Bottom navigation bar | **Out** — admin/SaaS dashboards (Vercel, Linear, Stripe, Datadog) all ship drawer-only on mobile |

---

## 2. Architecture

### 2.1 Layout Shell (`src/routes/_authed.tsx`)

Replace the hand-written `flex h-screen` shell with the shadcn Sidebar primitives:

```tsx
<ThemeProvider defaultTheme="system" storageKey="lolday-theme">
  <SidebarProvider>
    <AppSidebar />
    <SidebarInset>
      <TopBar />
      <main className="flex-1 overflow-y-auto bg-background p-4 md:p-6">
        <Outlet />
      </main>
    </SidebarInset>
  </SidebarProvider>
</ThemeProvider>
```

`SidebarProvider` reads viewport width and toggles between mobile drawer and desktop modes. `SidebarInset` handles the flex layout that today is hand-coded.

### 2.2 Sidebar (`src/components/layout/AppSidebar.tsx`, replaces `Sidebar.tsx`)

- Mobile (< 768 px): collapses to `0`; the `SidebarTrigger` (hamburger) in `TopBar` opens a vaul drawer.
- Desktop (≥ 768 px): toggles between 240 px expanded and 56 px icon-only. Cookie persists the choice across reloads.
- Active route receives `bg-sidebar-accent`.
- Icons: see §1.

### 2.3 Theme System

New file `src/components/ThemeProvider.tsx` (~30 lines, copied from shadcn Vite docs):

- `storageKey="lolday-theme"` — `localStorage` key.
- `defaultTheme="system"` — falls back to `prefers-color-scheme`.
- `setTheme()` writes `localStorage` and toggles `class="dark"` on `<html>`.

New file `src/components/ThemeToggle.tsx`:

- Uses shadcn `DropdownMenu` with three items: Light, Dark, System.
- Lives in `TopBar`'s right-hand slot, beside any future toolbar buttons.

CSS tokens (extend `src/index.css`). Copy the eight `--sidebar-*` HSL pairs from the shadcn Sidebar block defaults — both the `:root` (light) and `.dark` blocks — verbatim. The shape:

```css
:root {
  --sidebar: 0 0% 98%;
  --sidebar-foreground: 240 5.3% 26.1%;
  --sidebar-primary: 240 5.9% 10%;
  --sidebar-primary-foreground: 0 0% 98%;
  --sidebar-accent: 240 4.8% 95.9%;
  --sidebar-accent-foreground: 240 5.9% 10%;
  --sidebar-border: 220 13% 91%;
  --sidebar-ring: 217.2 91.2% 59.8%;
}
.dark {
  --sidebar: 240 5.9% 10%;
  --sidebar-foreground: 240 4.8% 95.9%;
  /* …six more matching dark values… */
}
```

`Sidebar.tsx`'s hardcoded `bg-slate-900 text-slate-100` goes away. All sidebar styling reads `bg-sidebar` / `text-sidebar-foreground`, which switch with the theme.

### 2.4 Hook (`src/hooks/useIsMobile.ts`)

Standard shadcn implementation using `matchMedia("(max-width: 767px)")`. Returns `boolean`. Subscribes once and unsubscribes on unmount. `SidebarProvider` already uses this internally; `DataTable` and any component that needs to switch render paths shares it.

User-agent sniffing is forbidden — it reports false positives on desktop with narrow windows and false negatives on iPad Safari.

---

## 3. List Pages

### 3.1 ColumnDef metadata (TanStack Table module augmentation)

New file `src/components/tables/types.ts`:

```ts
declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    /** Card-mode label (replaces column header). */
    cardLabel?: string;
    /** Card slot: title (large header), subtitle (top-right), body (label/value row), actions (top-right dropdown), hidden. */
    cardSlot?: "title" | "subtitle" | "body" | "actions" | "hidden";
    /** Body slot ordering; defaults to column order. */
    cardOrder?: number;
  }
}
```

Module augmentation is the official TanStack pattern, fully type-checked, no string lookups.

### 3.2 DataTable refactor (`src/components/tables/DataTable.tsx`)

Internal dispatch:

```tsx
const isMobile = useIsMobile();
return (
  <div className="space-y-3">
    {isMobile && <MobileSortBar table={table} />}
    {isMobile ? (
      <CardList
        table={table}
        onRowClick={onRowClick}
        emptyMessage={emptyMessage}
      />
    ) : (
      <DesktopTable
        table={table}
        onRowClick={onRowClick}
        emptyMessage={emptyMessage}
      />
    )}
    <Pagination table={table} />
  </div>
);
```

Sub-components live alongside `DataTable.tsx`:

- `CardList.tsx` — reads `meta.cardSlot` for each column; renders one `<Card>` per row, with `<dl>` body rows aligned baseline.
- `DesktopTable.tsx` — extracts today's `<Table>` rendering verbatim.
- `MobileSortBar.tsx` — `Select` of sortable columns, sets `table.setSorting()`.
- `Pagination.tsx` — Prev / page indicator / Next.

### 3.3 PageHeader (`src/components/layout/PageHeader.tsx`)

Today every list page hand-codes `<div className="flex items-center justify-between">…</div>`. The pattern wraps poorly on phones when filters + buttons share the row. Replace with one component:

```tsx
<PageHeader
  title="Jobs"
  actions={
    <>
      <Filter />
      <Button>Submit</Button>
    </>
  }
/>
// Internal layout:
// <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
//   <h1 className="text-xl sm:text-2xl font-semibold">{title}</h1>
//   <div className="flex flex-wrap items-center gap-2">{actions}</div>
// </div>
```

All `_index` routes plus the `.new` and `.$id` routes adopt it.

### 3.4 Affected list/table sites

- `_authed.detectors._index.tsx`
- `_authed.datasets._index.tsx`
- `_authed.jobs._index.tsx`
- `_authed.runs._index.tsx`
- `_authed.runs.$expId.tsx`
- `_authed.models._index.tsx`
- `_authed.detectors.$id.tsx` (versions table + builds table)
- `_authed.admin.users.tsx`

Each gets `meta.cardSlot` / `meta.cardLabel` on its `ColumnDef`s and switches its header row to `<PageHeader>`.

---

## 4. Detail Pages, Forms, Charts, Data Containers

### 4.1 Detail page grids

Two existing `grid grid-cols-2` literals do not collapse on mobile and must change:

- `src/routes/_authed.datasets.$id.tsx:43` — `grid-cols-2` → `grid-cols-1 sm:grid-cols-2`
- `src/components/jobs/JobDetailShell.tsx:61` — same fix

The other four `grid-cols-*` sites already use mobile-first variants and stay as-is.

### 4.2 Tabs

Wrap `TabsList` in `<ScrollArea orientation="horizontal">` (already installed) so tab strips never overflow. Apply as a baseline rule, not per-page.

### 4.3 Forms — Card padding, sticky CTA, button sizing

- shadcn `Card` defaults to `p-6`. Override to `p-4 sm:p-6` so phones reclaim 16 px on each side.
- `JobSubmitForm` job-type buttons: `flex gap-2` → `grid grid-cols-3 gap-2 sm:flex sm:flex-wrap`. Three equal-width buttons line up neatly at 360 px.
- Submit / Cancel row: `position: sticky; bottom: 0` inside the form on mobile, so the row sits at the bottom of the visible main scroll area regardless of form length. Add `pb-[env(safe-area-inset-bottom)]` to respect iOS home-indicator inset.
- Buttons inside forms: `h-11` (44 px) to meet WCAG 2.5.5 / Apple HIG touch targets.
- Inputs and selects: `w-full` on mobile (shadcn defaults already do this; audit forms for fixed widths).

### 4.4 RJSF mobile rules (in `index.css` under `.rjsf-wrap`)

```css
.rjsf-wrap {
  & input,
  & textarea,
  & select {
    width: 100%;
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

### 4.5 Charts

- `JobMetricChart.tsx` — already uses `ResponsiveContainer width="100%" height={320}`. No change.
- `LabelDistribution.tsx` — `ResponsiveContainer` is missing explicit `width` / `height`. Add `width="100%" height={240}`.
- `FamilyDistribution.tsx` — same fix.
- `ConfusionMatrix.tsx` — hand-rolled `inline-block` grid. Wrap the parent in `overflow-x-auto` so multi-class matrices scroll horizontally on phones.
- All recharts components: move `<Legend>` to bottom on mobile, shrink axis tick fonts one step.

### 4.6 Data containers

- `Sheet` (shadcn primitive) defaults to `w-3/4` on side variants. Change side variants to `w-full sm:max-w-sm` so manifest viewers and similar drawers fill the phone.
- `LogTail.tsx` — wrap log `<pre>` in `overflow-x-auto`.
- Long `git_url` / `git_sha` / file paths — apply `truncate` with `title=` attribute.

---

## 5. Phasing

Four PRs, in order. Each lands independently with passing tests.

### PR-1 — Foundation + Sidebar

Files created: `ui/sidebar.tsx`, `layout/AppSidebar.tsx`, `ThemeProvider.tsx`, `ThemeToggle.tsx`, `hooks/useIsMobile.ts`.
Files modified: `index.css`, `_authed.tsx`, `TopBar.tsx`.
Files deleted: `layout/Sidebar.tsx`.

Acceptance:

- ≥ 768 px: sidebar at 240 px expanded; toggle to 56 px icon-only; reload preserves state.
- < 768 px: sidebar hidden; hamburger in TopBar opens drawer; ESC or overlay click closes.
- Theme toggle has Light / Dark / System; selection persists across reload; System mode follows OS preference live.
- New icons readable at 22 px; icon-only mode shows no two icons that confuse.
- `pnpm typecheck && pnpm lint && pnpm test` green; existing Playwright E2E green.

### PR-2 — DataTable + list pages

Files created: `tables/types.ts`, `tables/CardList.tsx`, `tables/DesktopTable.tsx`, `tables/MobileSortBar.tsx`, `tables/Pagination.tsx`, `layout/PageHeader.tsx`.
Files modified: `tables/DataTable.tsx`, all routes listed in §3.4.

Acceptance:

- ≥ 768 px: every list page renders today's table, sort, pagination, actions.
- < 768 px: every list page renders cards with title / subtitle / body / actions slots; tap navigates to detail.
- Sort dropdown changes card order.
- Page headers stack vertically at 360 px and lay out horizontally at ≥ 640 px.
- TS module augmentation type-checks.
- Lint, types, tests, existing E2E green.

### PR-3 — Detail / Forms / Charts

Files modified: `_authed.datasets.$id.tsx`, `jobs/JobDetailShell.tsx`, all `forms/*.tsx`, `charts/{LabelDistribution,FamilyDistribution}.tsx`, `ui/sheet.tsx`, `index.css` (`.rjsf-wrap` rules).

Acceptance:

- 360 px viewport: every detail page scrolls vertically only — no horizontal overflow.
- `JobSubmitForm`, `DatasetUploadForm`, `RegisterDetectorForm`, `GitCredentialForm`, `DiscordIdForm`, `ModelTransitionDialog`: Card padding `p-4 sm:p-6` and Submit row sticks to viewport bottom on mobile with Cancel/Submit ≥ 44 px tall. (`JobSubmitForm` additionally moves job-type buttons into a three-column grid at base.)
- RJSF `ArrayField` add/remove buttons wrap rather than overflow at 360 px.
- All four charts render without clipping at 360 px.
- `ConfusionMatrix` scrolls horizontally for multi-class.
- `grep -rE "bg-slate-9[0-9]{2}|text-slate-1[0-9]{2}" src/` returns no results — no hardcoded slate colors remain.

### PR-4 — Mobile E2E

Files modified: `playwright.config.ts`.
Files created: `tests/e2e/mobile/sidebar-drawer.spec.ts`, `list-cards.spec.ts`, `form-sticky.spec.ts`, `theme.spec.ts`.

Acceptance:

- Playwright projects: iPhone 13 mini (393 × 852) and Pixel 5 (393 × 851).
- Drawer test: hamburger opens drawer, navigation closes drawer, admin link only for admin users.
- Cards test: Jobs list renders cards on mobile, tap row navigates, sort changes order.
- Sticky CTA test: open `/jobs/new`, submit button visible at viewport bottom without scrolling.
- Theme test: switch to dark, reload, dark persists.
- Existing desktop E2E green.

---

## 6. Out of Scope

- `next-themes` (Next.js-only; we use shadcn's Vite ThemeProvider).
- Bottom navigation bar (drawer-only matches admin/SaaS conventions).
- PWA / offline / install banner.
- Landscape-specific layouts (portrait-first; landscape inherits `sm:` styles).
- Color, font, or radius changes.
- i18n string expansion beyond the few new keys for nav menu and theme labels.

---

## 7. Open Questions

None remaining at design close. Implementation plan will surface any finer-grained dependencies (e.g., whether shadcn `ui/sidebar.tsx` requires a peer dependency we have not installed).
