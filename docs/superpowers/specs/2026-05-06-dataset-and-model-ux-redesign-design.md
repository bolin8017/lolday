# Dataset detail / Upload form / Models list — UX redesign — Design Specification

> Date: 2026-05-06
> Owner: PO-LIN LAI
> Status: design approved (brainstorm), pending implementation plan

## Overview

Four operator-reported issues across the dataset and model pages share a root pattern: each surface was wired to its API but never received a focused UX pass. The label-distribution chart picks colors by entry order rather than by semantic meaning; the family-distribution chart hard-codes Top 15 in a fixed-height box that becomes unreadable as families grow; the upload form mixes a stale demo placeholder with a native `<select>` that breaks dark mode and lacks both a Cancel button and frontend-side CSV validation; the models list shows MLflow Stage columns with no in-product explanation, so a brand-new operator sees only blanks.

This spec replaces the dataset detail page layout, refactors the two chart primitives with semantic color and long-tail aggregation, modernises the upload form to match the rest of the app, and adds an in-product Stage explainer to the models list. All changes follow shadcn/ui + Tailwind conventions already established in the codebase. No new dependencies are introduced.

**Design principles:**

- Mainstream over clever: shadcn/ui `Select` / `Collapsible` / `Tooltip`, recharts `BarChart` / `PieChart`, the same TanStack Table primitives the rest of the app uses.
- Root-cause fixes only: replace `LabelDistribution` color logic, replace `FamilyDistribution` aggregation, replace the native `<select>`, do not patch around them.
- Breaking changes accepted: backward compatibility is not a goal. Operators see new layout immediately on next deploy.
- Information hierarchy first: KPI strip → distribution shape → drill-down list, top to bottom.
- No new fonts, no new global tokens, no CSP relaxation. CSP `script-src 'self'` constraint remains intact.

---

## 1. Decisions Locked During Brainstorm

| Decision                       | Choice                                                                                                                                                                                       |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dataset detail page scope      | Full layout redesign (header → KPI strip → label distribution → family distribution → metadata details)                                                                                      |
| Family chart pattern           | Top 10 + Other(N) aggregate row; horizontal bars with count + % rendered inline; full searchable / sortable list inside `<Collapsible>` below                                                |
| Family chart bar density       | Fixed bar row height 32 px, container height = `(items + 1) × 36 px`, no scroll inside chart card                                                                                            |
| Label chart shape              | Donut (`innerRadius=60`) with center percent for the dominant class; legend is a 2-column inline table (count + %)                                                                           |
| Label color mapping            | Lookup `{ Malware: red-600 (#dc2626), Benign: green-600 (#16a34a) }`; unknown labels fall back to neutral `muted-foreground`                                                                 |
| Size field on dataset detail   | Removed from UI. `DatasetConfigRead.size_bytes` stays in the schema for API parity but is not rendered                                                                                       |
| Checksum                       | Moved into a collapsible "Metadata" details block at page bottom                                                                                                                             |
| Upload form Visibility control | shadcn `<Select>` (matches `JobSubmitForm`, `ModelTransitionDialog`); native `<select>` removed                                                                                              |
| Upload form name placeholder   | Replaced with neutral copy via i18n key (`datasets.new.namePlaceholder`); literal `upx-train-v3` deleted                                                                                     |
| Upload form Cancel button      | shadcn ghost `<Button onClick={() => nav(-1)}>` inside `<StickyFormFooter>`, matching `JobSubmitForm`                                                                                        |
| Upload form CSV pre-validation | Frontend mirrors backend rules where cheap to do client-side: ≥1 row, SHA256 regex on `file_name`, label ∈ {Malware, Benign}, `family` only on Malware rows. Backend remains source of truth |
| Models list Stage explainer    | Inline shadcn `<Alert>` above the table, dismissible, persisted in `localStorage`; column headers gain `<Tooltip>` icons; empty cells render `Not promoted` (muted) instead of `—`           |
| New backend endpoints          | None. All changes are frontend-only against existing schemas                                                                                                                                 |
| New i18n keys                  | Added under existing `datasets.*` and `models.*` namespaces in both `en.json` and `zh-TW.json`                                                                                               |

---

## 2. Architecture

### 2.1 Dataset detail page (`src/routes/_authed.datasets.$id.tsx`)

Replace the current two-card grid with a top-down sectioned layout:

```tsx
<div className="space-y-4">
  <DatasetHeader dataset={data} onDelete={onDelete} /> {/* §2.2 */}
  <DatasetKpiStrip dataset={data} /> {/* §2.3 */}
  <Card>
    <LabelDistribution data={data.label_distribution} />
  </Card>{" "}
  {/* §3.1 */}
  <Card>
    <FamilyDistribution data={data.family_distribution} />
  </Card>{" "}
  {/* §3.2 */}
  <DatasetMetadataDetails dataset={data} /> {/* §2.4 */}
</div>
```

The grid `md:grid-cols-2` placement of the two charts is removed: family distribution is full-width to give bars room.

### 2.2 Header (`src/components/datasets/DatasetHeader.tsx`)

- `<h1>` dataset name + visibility `<Badge>` inline
- `<p className="text-muted-foreground">` description (`—` when null)
- Right cluster: `<a download CSV>` styled as `<Button variant="outline">`, `<Button variant="destructive" Delete>`
- Mobile collapses the right cluster under the title (existing `flex-wrap` pattern)

### 2.3 KPI strip (`src/components/datasets/DatasetKpiStrip.tsx`)

A horizontal row of 5 stat tiles. Each tile is a `<Card>` with `text-2xl font-semibold tabular-nums` for the number and `text-xs text-muted-foreground` for the label.

| Tile     | Source                                               | Notes                    |
| -------- | ---------------------------------------------------- | ------------------------ |
| Samples  | `data.sample_count`                                  | `toLocaleString()`       |
| Malware  | `data.label_distribution["Malware"] ?? 0`            | red-600 number color     |
| Benign   | `data.label_distribution["Benign"] ?? 0`             | green-600 number color   |
| Families | `Object.keys(data.family_distribution ?? {}).length` | `0` when null            |
| Created  | `formatRelative(data.created_at)`                    | text only, no big number |

Layout: `grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2`. On 360 px viewports the strip wraps to two rows; that is acceptable.

### 2.4 Metadata details (`src/components/datasets/DatasetMetadataDetails.tsx`)

shadcn `<Collapsible>` collapsed by default. When expanded, shows:

- `Owner: <username>` (resolved via existing user query — out of scope if not currently exposed; fallback `data.owner_id`)
- `Checksum: <sha256>` with a copy-to-clipboard `<Button>` (shadcn pattern: clipboard API + 2 s "Copied" toast)

Size_bytes is **not** rendered. The existing description on the dataset header replaces the current Metadata card's description slot.

---

## 3. Chart components

### 3.1 LabelDistribution (`src/components/charts/LabelDistribution.tsx` — rewrite)

```ts
const LABEL_COLOR: Record<string, string> = {
  Malware: "#dc2626", // red-600
  Benign: "#16a34a", // green-600
};
const FALLBACK_COLOR = "hsl(var(--muted-foreground))";
```

Render path:

1. Sort entries deterministically: Malware → Benign → others alphabetical.
2. Compute `total = sum(values)`, `percent = value / total`.
3. Recharts `<PieChart>` → `<Pie innerRadius={60} outerRadius={90}>` (donut), no slice labels (avoid clutter at high imbalance).
4. `<Cell fill={LABEL_COLOR[name] ?? FALLBACK_COLOR} />`.
5. Center text inside the donut: dominant class name + `Math.round(percent * 100)%`.
6. Legend below the chart is a 2-column flex list:
   ```
   ● Malware        912    59.0%
   ● Benign         636    41.0%
   ```
   Numbers right-aligned with `tabular-nums`. Dot color matches `LABEL_COLOR`.

Empty-state copy unchanged ("No label data.").

### 3.2 FamilyDistribution (`src/components/charts/FamilyDistribution.tsx` — rewrite)

New helper `aggregateLongTail` (colocated, not exported elsewhere — single-file unit):

```ts
export function aggregateLongTail(
  data: Record<string, number>,
  topN = 10,
): { name: string; value: number; isOther?: boolean }[] {
  const sorted = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .map(([name, value]) => ({ name, value }));
  if (sorted.length <= topN) return sorted;
  const top = sorted.slice(0, topN);
  const tailCount = sorted.length - topN;
  const tailSum = sorted.slice(topN).reduce((acc, x) => acc + x.value, 0);
  return [
    ...top,
    { name: `Other (${tailCount})`, value: tailSum, isOther: true },
  ];
}
```

Render path:

1. Compute `total = sum(values)` over **all** entries (not just the displayed ones); percentages are share-of-total.
2. Compute `bars = aggregateLongTail(data, 10)`.
3. Recharts `<BarChart layout="vertical" data={bars} barCategoryGap={4}>` with:
   - `<XAxis type="number" hide />` (the bar's right-edge label carries the value, axis is redundant)
   - `<YAxis type="category" dataKey="name" width={120} interval={0} />`
   - `<Bar dataKey="value">` with per-cell color: top 10 = `hsl(var(--primary))`, Other = `hsl(var(--muted-foreground))` so the aggregate is visually distinct
   - `<LabelList dataKey="value">` with custom formatter: `${value} (${pct}%)`, positioned `right`, `tabular-nums`
4. Container height: `(bars.length + 1) * 36`, capped at `360 px` (desktop) / `280 px` (mobile).
5. Card title shows `Top families` + a muted suffix: `Showing top 10 of {totalFamilies}` (suffix omitted when ≤ 10).
6. Below the chart, a shadcn `<Collapsible>` titled `Show all {n} families`. When expanded:
   - `<Input placeholder="Search families…" />` filters in-memory by case-insensitive substring.
   - shadcn `<Table>` with columns: `family` (sortable), `count` (sortable, default desc), `%` (computed), `rank` (1-indexed by default sort).

Empty-state copy unchanged ("No family data.").

### 3.3 Recharts theme alignment

Both charts already render inside cards that pick up `--background` / `--foreground`. No new CSS tokens. Use `hsl(var(--primary))` instead of hard-coded blue for the family bars so they switch with theme.

---

## 4. Upload form (`src/components/forms/DatasetUploadForm.tsx`)

### 4.1 Layout & structure unchanged at the form level

The two-tab CSV input (file picker / paste) and the preview table stay. Only the controls inside the form change.

### 4.2 Visibility control

Replace the native `<select>` (lines 99–107 in the current file) with shadcn `<Select>`:

```tsx
<FormField label="Visibility">
  <Select
    value={visibility}
    onValueChange={(v) => setValue("visibility", v as "public" | "private")}
  >
    <SelectTrigger>
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="public">Public (all lab members)</SelectItem>
      <SelectItem value="private">Private (me + admin)</SelectItem>
    </SelectContent>
  </Select>
</FormField>
```

No new wrapper component is needed — the existing `<Label>` + control pattern in this file already mirrors `JobSubmitForm`. Wiring goes through `react-hook-form`'s `setValue` (no `register` for shadcn Select; same approach as the rest of the codebase).

### 4.3 Name placeholder

Replace literal `placeholder="upx-train-v3"` with `placeholder={t("datasets.new.namePlaceholder")}`. New i18n entry:

- `en.json`: `"namePlaceholder": "e.g. malware-train-2026-q1"`
- `zh-TW.json`: `"namePlaceholder": "例如：malware-train-2026-q1"`

(The example string itself is intentionally generic — no real release name. Using zh-TW keeps the lab's first-class language consistent.)

### 4.4 Cancel button

Inside `<StickyFormFooter>`, add to the **left** of the existing submit button:

```tsx
<Button type="button" variant="ghost" className="h-11" onClick={() => nav(-1)}>
  {t("common.cancel")}
</Button>
```

`common.cancel` already exists in both locale files (used by `ModelTransitionDialog`). Reuse it; do not duplicate.

### 4.5 CSV pre-validation

Augment `parseCsvPreview` in `src/lib/csv.ts` (existing file) — **do not** create a parallel validator. The function already validates required columns. Extend it to also validate row content:

```ts
const SHA256_RE = /^[0-9a-f]{64}$/;
const VALID_LABELS = new Set(["Malware", "Benign"]);

// inside the row loop:
if (!SHA256_RE.test(row.file_name)) {
  throw new Error(
    `Row ${rowNum}: file_name must be 64-char lowercase hex (SHA256), got: ${row.file_name}`,
  );
}
if (!VALID_LABELS.has(row.label)) {
  throw new Error(
    `Row ${rowNum}: label must be Malware or Benign, got: ${row.label}`,
  );
}
if (row.family && row.label !== "Malware") {
  throw new Error(
    `Row ${rowNum}: family is only allowed on Malware rows, got: label=${row.label} family=${row.family}`,
  );
}
```

These rules are deliberately a **strict subset** of `backend/app/services/dataset.py:parse_csv` so frontend never accepts what backend rejects. Backend remains the source of truth; frontend only enables fast feedback.

The existing `parseError` Alert renders the thrown message verbatim, so users see the failing row number.

Preview row limit stays at 10. When validation fails on row N>10, the message is still surfaced (validation runs on the entire body, preview is a separate concern).

### 4.6 No size badge above the form

Already not rendered today. Confirming for clarity: do not add one. The 10 MB hard limit (`MAX_CSV_BYTES`) keeps its existing on-submit error path.

---

## 5. Models list page (`src/routes/_authed.models._index.tsx`)

### 5.1 Inline Stage explainer

Above `<DataTable>`, add a dismissible `<Alert>` (shadcn) with copy:

> **About model stages**
> Newly registered model versions start in **None**. Use the **Transition** button on each version to promote: **Staging** for candidates under review, **Production** for the live serving version, **Archived** for retired versions. Mirrors the MLflow Model Registry lifecycle.

Dismissed state persists in `localStorage` under the key `lolday.modelsExplainerDismissed=1`. Reading the key on mount before paint avoids a flash. (Same pattern as `lolday-theme` in §2.3 of the mobile redesign spec.)

i18n: full alert text goes under `models.stagesExplainer.title` and `models.stagesExplainer.body` in both locale files.

### 5.2 Column header tooltips

Wrap the `Staging` and `Production` header strings with shadcn `<Tooltip>`:

- Staging tooltip: `Candidate version under validation. Promote here before production.`
- Production tooltip: `Currently the active version. Only one Production version per model at a time.`

A `<HelpCircle>` lucide icon (size 14) sits inline with the header text.

### 5.3 Empty cell wording

Change `<span className="text-muted-foreground">—</span>` to `<span className="text-muted-foreground">{t("models.notPromoted")}</span>` — text `Not promoted`.

i18n adds `models.notPromoted: "Not promoted" / "尚未升等"`.

---

## 6. File map

```
frontend/src/
  routes/_authed.datasets.$id.tsx        — rewrite to sectioned layout
  routes/_authed.models._index.tsx       — add Alert, Tooltip, empty wording
  components/datasets/DatasetHeader.tsx          — new
  components/datasets/DatasetKpiStrip.tsx        — new
  components/datasets/DatasetMetadataDetails.tsx — new
  components/charts/LabelDistribution.tsx        — rewrite (donut + semantic colors)
  components/charts/FamilyDistribution.tsx       — rewrite (Top 10 + Other + collapsible table)
  components/forms/DatasetUploadForm.tsx         — Cancel button, shadcn Select, i18n placeholder
  lib/csv.ts                                     — extend parseCsvPreview validation
  i18n/en.json                                   — new keys (datasets.new.*, models.*)
  i18n/zh-TW.json                                — new keys, zh-TW first-class

frontend/tests/unit/
  components/charts/LabelDistribution.test.tsx        — new (color mapping, donut center)
  components/charts/FamilyDistribution.test.tsx       — new (aggregateLongTail edges, all-families table search)
  components/datasets/DatasetKpiStrip.test.tsx        — new
  components/forms/DatasetUploadForm.test.tsx         — extend for Cancel + shadcn Select + new validation errors
  lib/csv.test.ts                                     — extend for SHA256 / label / family rules
```

No backend file changes. No new dependencies.

---

## 7. Testing

### 7.1 Unit (vitest)

- **`aggregateLongTail`**: 0, 1, 9, 10, 11, 100 entries; verify Top-N and Other count + sum, Other suppressed when total ≤ topN.
- **`LabelDistribution`**: Malware-only, Benign-only, both, unknown label gets fallback color, donut renders `total = 0` empty-state.
- **`FamilyDistribution`**: empty data → empty-state copy, ≤10 families → no Other row, >10 → Other row exists, search input filters table case-insensitively, sorting by count default desc.
- **`DatasetKpiStrip`**: with/without family_distribution, formatting of large counts, Mal/Ben fallback to 0.
- **`DatasetUploadForm`**: Cancel calls `navigate(-1)` (already mocked in existing tests), shadcn Select emits the chosen value, name input renders the new i18n placeholder.
- **`parseCsvPreview`** (existing test file): SHA256 fail, label fail, family-on-Benign fail; existing tests for size and required cols continue to pass.

### 7.2 Component / E2E

- Existing Playwright happy-path on `/datasets/:id` — assert KPI tile counts, donut center renders, family chart renders Top 10 + Other for a 12-family dataset.
- Existing E2E for `/datasets/new` — Cancel returns to list, Visibility select changes color in dark mode, CSV with bad SHA256 surfaces the row error before submit.
- New E2E for models list — explainer Alert renders on first visit; after Dismiss, reload does not re-render the alert; tooltip on `Staging` header reveals copy.

### 7.3 Manual smoke (per `frontend.md` rules)

- `pnpm dev` → light + dark mode walkthroughs of `/datasets/:id`, `/datasets/new`, `/models`.
- Production CSP smoke: build the frontend image and load the same pages from the deployed nginx; confirm no CSP violations in DevTools console (defending against accidental inline scripts in chart libs).

---

## 8. i18n inventory

New keys (existing files only — `en.json`, `zh-TW.json`):

```jsonc
{
  "common": { "cancel": "..." /* unchanged */ },
  "datasets": {
    "new": {
      "namePlaceholder": "e.g. malware-train-2026-q1" /* zh-TW: 例如：malware-train-2026-q1 */,
    },
    "detail": {
      "kpi": {
        "samples": "...",
        "malware": "...",
        "benign": "...",
        "families": "...",
        "created": "...",
      },
      "showAllFamilies": "Show all {{n}} families",
      "searchFamilies": "Search families…",
      "topOf": "Showing top {{shown}} of {{total}}",
    },
  },
  "models": {
    "stagesExplainer": {
      "title": "About model stages",
      "body": "Newly registered model versions start in None. Use the Transition button to promote them: Staging for candidates under review, Production for the live serving version, Archived for retired versions. Mirrors the MLflow Model Registry lifecycle.",
      "dismiss": "Dismiss",
    },
    "notPromoted": "Not promoted",
    "stages": {
      "stagingTooltip": "Candidate version under validation. Promote here before production.",
      "productionTooltip": "Currently the active version. Only one Production version per model at a time.",
    },
  },
}
```

zh-TW translations are first-class. English is a translation, not the canonical source.

---

## 9. Out of scope

- Any backend schema or endpoint change. `DatasetConfigRead.size_bytes` stays in the API even though the UI hides it.
- Renaming MLflow stage values. Mirrors MLflow Model Registry, do not invent new stages.
- New auth or permissions for promote / dismiss. Existing role checks in `services/model_registry.py` stand.
- Multi-language families table content. Family names are lowercase ASCII identifiers (`mirai`, `dridex`); no translation.
- Sample-storage size accounting. The decision to drop `size_bytes` from UI is final for this redesign; if a meaningful "real dataset size" metric is needed later (samples × avg sample size), open a separate spec.
- Treemap, sunburst, or alternative chart types. Top-N + Other + table is the locked-in pattern.
- Export of family list to CSV. Out of scope; reachable via the dataset's own CSV download.
- Webfont introduction. CSP `script-src 'self'` constraint is honored as a hard rule.

---

## 10. Risks and mitigations

| Risk                                                                                  | Mitigation                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Recharts `<LabelList>` text overlaps short bars when a single family dominates        | Use `position="right"` (renders outside the bar); on bars whose computed end-X is within 80 px of the chart edge, switch to `position="insideRight"` via the formatter — same trick as W&B's experiment view |
| `localStorage.lolday.modelsExplainerDismissed` lingers after we change explainer copy | Rename the key when the copy materially changes. For this rollout the key is fresh, so no migration needed                                                                                                   |
| `aggregateLongTail` mutates input                                                     | Helper builds a new array — covered by a unit test that asserts input identity                                                                                                                               |
| Donut center text overflows on 360 px viewport                                        | Use `responsive container` width detection; below 320 px center, drop to the percent only (no class name)                                                                                                    |
| `Other (N)` covers an interesting tail family                                         | Acceptable: the collapsible table below shows the full sortable list. Mirrors W&B / Vertex / Looker conventions                                                                                              |
| MLflow stage explainer Alert competes for vertical space on mobile                    | Alert respects `dismiss` and persists; first-visit cost is one `<Alert>` height, acceptable per `mobile-responsive-redesign` spec §3                                                                         |
| Long family names (e.g. `Trojan-Ransom.Win32.Locker`) truncate at the 120 px y-axis   | Acceptable on the bar chart — full name is always visible in the collapsible table below. Tooltip on hover surfaces the full name without resizing the axis                                                  |

---

## 11. Done criteria

- `/datasets/:id` renders the new sectioned layout. Label colors are red (Malware) and green (Benign). Family chart shows Top 10 + Other(N) with inline count + %, plus a collapsible searchable table.
- `/datasets/new` has a working Cancel button, shadcn-themed Visibility select that respects dark mode, validates row content (SHA256 / label / family-on-Malware) before submit, and uses the new placeholder.
- `/models` shows a dismissible Stage explainer Alert, tooltip icons on `Staging` / `Production` headers, and `Not promoted` for empty cells.
- All existing E2E and unit tests continue to pass; new tests in §7 cover the changes.
- `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test` green from `frontend/`.
- Manual smoke on built nginx image: no CSP violations, light/dark mode parity intact.

---

## 12. References

- Mainstream long-tail pattern: Weights & Biases experiment dashboards, GCP Vertex AI Workbench feature distribution, Looker categorical "Other" aggregation default.
- shadcn/ui Select / Collapsible / Tooltip / Alert — already used elsewhere in this codebase.
- Existing precedent: `JobSubmitForm.tsx` (Cancel button + shadcn Select), `ModelTransitionDialog.tsx` (shadcn Select + Tooltip pattern).
- MLflow Model Registry stage lifecycle: <https://mlflow.org/docs/latest/model-registry.html#model-stages>.
- `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` — sister spec; this redesign honors its breakpoints, theme tokens, and `useIsMobile` hook.
