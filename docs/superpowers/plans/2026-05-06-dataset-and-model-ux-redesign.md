# Dataset & Model UX Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the four UX changes captured in `docs/superpowers/specs/2026-05-06-dataset-and-model-ux-redesign-design.md`: redesign the dataset detail page (KPI strip + Top-N + Other family chart + semantic-color label donut), modernize the upload form (shadcn Select + Cancel + CSV row validation), and add an MLflow stage explainer to the models list. Frontend-only.

**Architecture:** Pure-logic helpers (`aggregateLongTail`, extended `parseCsvPreview`) ship behind `*.logic.ts` siblings of the consuming components, mirroring `DatasetUploadForm.logic.ts` / `JobSubmitForm.logic.ts`. Charts and form controls all sit on shadcn/ui primitives + Recharts; no new dependency beyond the shadcn `Collapsible` block. The dataset detail route decomposes into focused presentation components (`DatasetHeader`, `DatasetKpiStrip`, `DatasetMetadataDetails`) so the route file becomes a thin assembler.

**Tech Stack:** Vite + React 18 + TypeScript 5.5, shadcn/ui (Radix primitives), Tailwind 3.4, Recharts, react-router 7, react-i18next, react-hook-form, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-05-06-dataset-and-model-ux-redesign-design.md`

**Working directory for all `pnpm` commands:** `frontend/`

---

## Task 1: Add i18n keys for redesign

Establish the translation surface first so every later task can reference final keys without churn.

**Files:**

- Modify: `frontend/src/i18n/en.json`
- Modify: `frontend/src/i18n/zh-TW.json`

- [ ] **Step 1: Add new keys to `frontend/src/i18n/en.json`**

Insert new branches into the existing JSON. Place `datasets` after the existing `stage` block (before `jobs`). Place `models` after `jobs`. Append `chart` keys to the existing `common` block. Final additions (literal — keep nested per `.claude/rules/frontend.md` i18n convention; do NOT use flat dot-keys):

```jsonc
// Inside "common":
"copy":           "Copy",
"copied":         "Copied",
"showAll":        "Show all",
"search":         "Search",
"dismiss":        "Dismiss",
```

```jsonc
// New top-level "datasets":
"datasets": {
  "new": {
    "namePlaceholder": "e.g. malware-train-2026-q1",
    "submitting":      "Uploading…"
  },
  "detail": {
    "kpi": {
      "samples":  "Samples",
      "malware":  "Malware",
      "benign":   "Benign",
      "families": "Families",
      "created":  "Created"
    },
    "labelDistribution":  "Label distribution",
    "familyDistribution": "Family distribution",
    "topOf":              "Showing top {{shown}} of {{total}}",
    "showAllFamilies":    "Show all {{n}} families",
    "searchFamilies":     "Search families…",
    "metadata":           "Metadata",
    "checksum":           "Checksum",
    "owner":              "Owner",
    "noLabelData":        "No label data.",
    "noFamilyData":       "No family data.",
    "tableFamily":        "Family",
    "tableCount":         "Count",
    "tablePercent":       "Share",
    "tableRank":          "Rank"
  }
}
```

```jsonc
// New top-level "models":
"models": {
  "stagesExplainer": {
    "title": "About model stages",
    "body":  "Newly registered model versions start in None. Use the Transition button on each version to promote: Staging for candidates under review, Production for the live serving version, Archived for retired versions. Mirrors the MLflow Model Registry lifecycle."
  },
  "stages": {
    "stagingTooltip":    "Candidate version under validation. Promote here before production.",
    "productionTooltip": "Currently the active version. Only one Production version per model at a time."
  },
  "notPromoted": "Not promoted"
}
```

- [ ] **Step 2: Add the matching zh-TW keys to `frontend/src/i18n/zh-TW.json`**

```jsonc
// Inside "common":
"copy":    "複製",
"copied":  "已複製",
"showAll": "顯示全部",
"search":  "搜尋",
"dismiss": "關閉",
```

```jsonc
"datasets": {
  "new": {
    "namePlaceholder": "例如：malware-train-2026-q1",
    "submitting":      "上傳中…"
  },
  "detail": {
    "kpi": {
      "samples":  "樣本",
      "malware":  "惡意",
      "benign":   "良性",
      "families": "家族",
      "created":  "建立時間"
    },
    "labelDistribution":  "標籤分布",
    "familyDistribution": "家族分布",
    "topOf":              "顯示前 {{shown}} 名 / 共 {{total}} 個",
    "showAllFamilies":    "顯示全部 {{n}} 個家族",
    "searchFamilies":     "搜尋家族…",
    "metadata":           "Metadata",
    "checksum":           "Checksum",
    "owner":              "擁有者",
    "noLabelData":        "無標籤資料。",
    "noFamilyData":       "無家族資料。",
    "tableFamily":        "家族",
    "tableCount":         "數量",
    "tablePercent":       "比例",
    "tableRank":          "排名"
  }
}
```

```jsonc
"models": {
  "stagesExplainer": {
    "title": "關於模型階段",
    "body":  "新註冊的模型版本預設為 None。點選版本旁的「Transition」按鈕進行升等：Staging 為待驗證候選版本、Production 為線上服務版本、Archived 為已退役版本。沿用 MLflow Model Registry 的生命週期。"
  },
  "stages": {
    "stagingTooltip":    "候選版本，等待驗證。升 Production 前的暫存階段。",
    "productionTooltip": "目前線上服務版本。同一模型同時只允許一個 Production 版本。"
  },
  "notPromoted": "尚未升等"
}
```

- [ ] **Step 3: Verify JSON parses**

Run: `cd frontend && pnpm typecheck`
Expected: PASS — both i18n imports compile (i18n loads JSON at runtime, but tsc validates the import shape).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/en.json frontend/src/i18n/zh-TW.json
git commit -m "$(cat <<'EOF'
feat(frontend): add i18n keys for dataset and model UX redesign

Lays the translation surface for the dataset detail KPI strip,
chart legends and Top-N + Other family table, the upload-form
namePlaceholder, and the MLflow stage explainer on the models
list. Spec §8 inventory.
EOF
)"
```

---

## Task 2: Add shadcn Collapsible primitive

The redesign uses `<Collapsible>` for the "Show all N families" drill-down and the dataset Metadata details block. shadcn ships it as an officially supported block.

**Files:**

- Create: `frontend/src/components/ui/collapsible.tsx` (via shadcn CLI)
- Modify: `frontend/package.json` (auto by `pnpm dlx shadcn` adding `@radix-ui/react-collapsible`)

- [ ] **Step 1: Run shadcn CLI from `frontend/`**

```bash
cd frontend && pnpm dlx shadcn@latest add collapsible
```

Expected: creates `src/components/ui/collapsible.tsx`, adds `@radix-ui/react-collapsible` to `package.json` and `pnpm-lock.yaml`. Reading from auto memory: shadcn CLI sometimes also writes side-effects (e.g. `src/hooks/use-mobile.tsx` or extra CSS tokens) — **before commit**, run `git status` and verify only `components/ui/collapsible.tsx`, `package.json`, `pnpm-lock.yaml` show changes. If anything else appears, delete those files.

- [ ] **Step 2: Verify the primitive imports cleanly**

Run: `cd frontend && pnpm typecheck`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/collapsible.tsx frontend/package.json frontend/pnpm-lock.yaml
git commit -m "$(cat <<'EOF'
feat(frontend): add shadcn Collapsible primitive

Required by the redesigned FamilyDistribution drill-down and the
dataset Metadata block. Copied verbatim from `pnpm dlx shadcn add
collapsible`; no custom tokens.
EOF
)"
```

---

## Task 3: Add `aggregateLongTail` helper (TDD)

Pure function, easy to TDD. Place beside the consuming component as `FamilyDistribution.logic.ts` to follow `DatasetUploadForm.logic.ts` / `JobSubmitForm.logic.ts` precedent.

**Files:**

- Create: `frontend/src/components/charts/FamilyDistribution.logic.ts`
- Test: `frontend/tests/unit/FamilyDistribution.logic.test.ts`

- [ ] **Step 1: Write failing tests at `frontend/tests/unit/FamilyDistribution.logic.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { aggregateLongTail } from "@/components/charts/FamilyDistribution.logic";

describe("aggregateLongTail", () => {
  it("returns empty array on empty input", () => {
    expect(aggregateLongTail({})).toEqual([]);
  });

  it("returns sorted entries when count <= topN", () => {
    expect(aggregateLongTail({ a: 1, b: 3, c: 2 }, 10)).toEqual([
      { name: "b", value: 3 },
      { name: "c", value: 2 },
      { name: "a", value: 1 },
    ]);
  });

  it("returns exactly topN when input length equals topN", () => {
    const data = Object.fromEntries(
      Array.from({ length: 10 }, (_, i) => [`f${i}`, 10 - i]),
    );
    const out = aggregateLongTail(data, 10);
    expect(out).toHaveLength(10);
    expect(out.some((b) => b.isOther)).toBe(false);
  });

  it("aggregates the long tail into Other(N) when count > topN", () => {
    const data = Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => [`f${i}`, 12 - i]),
    );
    const out = aggregateLongTail(data, 10);
    expect(out).toHaveLength(11);
    const last = out[out.length - 1];
    expect(last.isOther).toBe(true);
    expect(last.name).toBe("Other (2)");
    expect(last.value).toBe(1 + 2); // f10=2, f11=1
  });

  it("sorts top entries descending by value, ties broken by insertion order", () => {
    const out = aggregateLongTail({ z: 5, a: 5, m: 1 }, 10);
    expect(out.map((b) => b.name)).toEqual(["z", "a", "m"]);
  });

  it("does not mutate the input object", () => {
    const data = { a: 1, b: 2 };
    const snapshot = { ...data };
    aggregateLongTail(data, 10);
    expect(data).toEqual(snapshot);
  });
});
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd frontend && pnpm test -- FamilyDistribution.logic.test`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `frontend/src/components/charts/FamilyDistribution.logic.ts`**

```typescript
export interface FamilyBar {
  name: string;
  value: number;
  isOther?: boolean;
}

export function aggregateLongTail(
  data: Record<string, number>,
  topN = 10,
): FamilyBar[] {
  const sorted: FamilyBar[] = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .map(([name, value]) => ({ name, value }));
  if (sorted.length <= topN) return sorted;
  const top = sorted.slice(0, topN);
  const tail = sorted.slice(topN);
  const tailSum = tail.reduce((acc, bar) => acc + bar.value, 0);
  return [
    ...top,
    { name: `Other (${tail.length})`, value: tailSum, isOther: true },
  ];
}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd frontend && pnpm test -- FamilyDistribution.logic.test`
Expected: PASS, all 6 cases green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/charts/FamilyDistribution.logic.ts frontend/tests/unit/FamilyDistribution.logic.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): aggregateLongTail helper for family distribution

Pure function that sorts a categorical count map descending and
collapses entries past topN into a single Other (N) row. Used by
the rewritten FamilyDistribution chart in the next commit.
EOF
)"
```

---

## Task 4: Extend `parseCsvPreview` row validation (TDD)

Mirror the strict subset of backend rules so invalid CSVs fail before the API call. Backend remains source of truth.

**Files:**

- Modify: `frontend/src/lib/csv.ts`
- Test: `frontend/tests/unit/lib/csvPreview.test.ts` (new — there is no existing csv test)

- [ ] **Step 1: Write failing tests at `frontend/tests/unit/lib/csvPreview.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { parseCsvPreview } from "@/lib/csv";

const HEX = "a".repeat(64);
const HEX2 = "b".repeat(64);

describe("parseCsvPreview", () => {
  it("returns rows when CSV is valid (existing behaviour)", () => {
    const csv = `file_name,label\n${HEX},Malware\n${HEX2},Benign\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(2);
    expect(out.columns).toEqual(["file_name", "label"]);
  });

  it("rejects missing required columns", () => {
    expect(() => parseCsvPreview("a,b\n1,2\n", 5)).toThrowError(
      /Missing required column/,
    );
  });

  it("rejects when file_name is not a 64-char lowercase hex SHA256", () => {
    const csv = `file_name,label\nDEADBEEF,Malware\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/SHA256/i);
  });

  it("rejects when label is not Malware or Benign", () => {
    const csv = `file_name,label\n${HEX},Suspicious\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(
      /label must be Malware or Benign/,
    );
  });

  it("rejects when family is set on a Benign row", () => {
    const csv = `file_name,label,family\n${HEX},Benign,mirai\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/family.*Malware/i);
  });

  it("accepts family on Malware rows", () => {
    const csv = `file_name,label,family\n${HEX},Malware,mirai\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(1);
  });

  it("rejects when there are no data rows", () => {
    expect(() => parseCsvPreview("file_name,label\n", 5)).toThrowError(
      /no data rows/i,
    );
  });

  it("includes the row number in error messages", () => {
    const csv = `file_name,label\n${HEX},Malware\nbadhash,Benign\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/Row 3/);
  });
});
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `cd frontend && pnpm test -- csvPreview.test`
Expected: FAIL — current `parseCsvPreview` only checks required columns and trims, so the validation cases pass through.

- [ ] **Step 3: Update `frontend/src/lib/csv.ts`**

Replace the file with:

```typescript
export interface CsvPreview {
  columns: string[];
  rows: Record<string, string>[];
  totalRows: number;
}

const REQUIRED = ["file_name", "label"];
const SHA256_RE = /^[0-9a-f]{64}$/;
const VALID_LABELS = new Set(["Malware", "Benign"]);

export function parseCsvPreview(text: string, limit = 20): CsvPreview {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) throw new Error("Empty CSV");
  const columns = splitLine(lines[0]);
  for (const req of REQUIRED) {
    if (!columns.includes(req))
      throw new Error(`Missing required column: ${req}`);
  }
  const dataLines = lines.slice(1).filter((l) => l.length > 0);
  if (dataLines.length === 0) throw new Error("CSV has no data rows");

  const fileNameIdx = columns.indexOf("file_name");
  const labelIdx = columns.indexOf("label");
  const familyIdx = columns.indexOf("family"); // -1 if absent

  for (let i = 0; i < dataLines.length; i++) {
    const cells = splitLine(dataLines[i]);
    const rowNum = i + 2; // 1-indexed + header line
    const fileName = (cells[fileNameIdx] ?? "").trim();
    const label = (cells[labelIdx] ?? "").trim();

    if (!SHA256_RE.test(fileName)) {
      throw new Error(
        `Row ${rowNum}: file_name must be 64-char lowercase hex SHA256, got: ${fileName || "(empty)"}`,
      );
    }
    if (!VALID_LABELS.has(label)) {
      throw new Error(
        `Row ${rowNum}: label must be Malware or Benign, got: ${label || "(empty)"}`,
      );
    }
    if (familyIdx >= 0) {
      const family = (cells[familyIdx] ?? "").trim();
      if (family && label !== "Malware") {
        throw new Error(
          `Row ${rowNum}: family is only allowed on Malware rows, got: label=${label}`,
        );
      }
    }
  }

  const rows = dataLines.slice(0, limit).map((line) => {
    const cells = splitLine(line);
    return Object.fromEntries(columns.map((c, i) => [c, cells[i] ?? ""]));
  });
  return { columns, rows, totalRows: dataLines.length };
}

// RFC 4180 minimal — handles quoted fields with commas/quotes.
function splitLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') inQuote = false;
      else cur += ch;
    } else {
      if (ch === ",") {
        out.push(cur);
        cur = "";
      } else if (ch === '"') inQuote = true;
      else cur += ch;
    }
  }
  out.push(cur);
  return out;
}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd frontend && pnpm test -- csvPreview.test`
Expected: PASS, all 8 cases green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/csv.ts frontend/tests/unit/lib/csvPreview.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): tighten CSV preview validation to mirror backend

parseCsvPreview now rejects rows with non-SHA256 file_name, labels
outside {Malware, Benign}, and family values set on Benign rows.
Surfaces the row number so users can locate the offending line.
Backend `parse_csv` remains the source of truth.
EOF
)"
```

---

## Task 5: Rewrite `LabelDistribution` (donut + semantic colors)

**Files:**

- Modify: `frontend/src/components/charts/LabelDistribution.tsx` (full rewrite)
- Test: `frontend/tests/unit/components/charts/LabelDistribution.test.tsx` (new — directory is also new)

- [ ] **Step 1: Replace the contents of `frontend/src/components/charts/LabelDistribution.tsx`**

```tsx
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { useTranslation } from "react-i18next";

const LABEL_COLOR: Record<string, string> = {
  Malware: "#dc2626", // red-600
  Benign: "#16a34a", // green-600
};
const FALLBACK_COLOR = "hsl(var(--muted-foreground))";

const LABEL_ORDER = ["Malware", "Benign"];

interface Entry {
  name: string;
  value: number;
}

function colorFor(name: string): string {
  return LABEL_COLOR[name] ?? FALLBACK_COLOR;
}

function sortEntries(data: Record<string, number>): Entry[] {
  const known: Entry[] = [];
  const unknown: Entry[] = [];
  for (const [name, value] of Object.entries(data)) {
    if (LABEL_ORDER.includes(name)) known.push({ name, value });
    else unknown.push({ name, value });
  }
  known.sort(
    (a, b) => LABEL_ORDER.indexOf(a.name) - LABEL_ORDER.indexOf(b.name),
  );
  unknown.sort((a, b) => a.name.localeCompare(b.name));
  return [...known, ...unknown];
}

export function LabelDistribution({ data }: { data: Record<string, number> }) {
  const { t } = useTranslation();
  const entries = sortEntries(data);
  const total = entries.reduce((acc, e) => acc + e.value, 0);
  if (entries.length === 0 || total === 0)
    return (
      <p className="text-muted-foreground">
        {t("datasets.detail.noLabelData")}
      </p>
    );

  const dominant = [...entries].sort((a, b) => b.value - a.value)[0];
  const dominantPct = Math.round((dominant.value / total) * 100);

  return (
    <div className="flex flex-col items-stretch gap-4 sm:flex-row sm:items-center">
      <div className="relative h-[200px] w-full sm:w-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={entries}
              dataKey="value"
              nameKey="name"
              innerRadius={60}
              outerRadius={90}
              isAnimationActive={false}
              stroke="hsl(var(--background))"
              strokeWidth={2}
            >
              {entries.map((e) => (
                <Cell key={e.name} fill={colorFor(e.name)} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div
          className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"
          aria-hidden
        >
          <span className="text-xl font-semibold tabular-nums">
            {dominantPct}%
          </span>
          <span className="text-xs text-muted-foreground">{dominant.name}</span>
        </div>
      </div>
      <ul className="grid flex-1 grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-sm">
        {entries.map((e) => {
          const pct = ((e.value / total) * 100).toFixed(1);
          return (
            <li key={e.name} className="contents">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: colorFor(e.name) }}
                />
                {e.name}
              </span>
              <span className="text-right tabular-nums text-muted-foreground">
                {e.value.toLocaleString()}
              </span>
              <span className="text-right tabular-nums text-muted-foreground">
                {pct}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Add component test at `frontend/tests/unit/components/charts/LabelDistribution.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LabelDistribution } from "@/components/charts/LabelDistribution";

describe("<LabelDistribution>", () => {
  it("renders empty state when data is empty", () => {
    render(<LabelDistribution data={{}} />);
    expect(screen.getByText("No label data.")).toBeInTheDocument();
  });

  it("renders both classes with counts and percentages", () => {
    render(<LabelDistribution data={{ Malware: 60, Benign: 40 }} />);
    expect(screen.getByText("Malware")).toBeInTheDocument();
    expect(screen.getByText("Benign")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("60.0%")).toBeInTheDocument();
    expect(screen.getByText("40.0%")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument(); // donut center (rounded)
  });

  it("falls back to neutral color for unknown labels", () => {
    // Smoke test: should render without throwing and include the unknown label.
    render(<LabelDistribution data={{ Malware: 10, Suspicious: 5 }} />);
    expect(screen.getByText("Suspicious")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend && pnpm test -- LabelDistribution.test`
Expected: PASS, all 3 cases green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/charts/LabelDistribution.tsx frontend/tests/unit/components/charts/LabelDistribution.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): semantic-color donut for LabelDistribution

Malware now always renders red, Benign green; legend is a tabular
inline grid with count + share. Donut center surfaces the dominant
class share rounded to a whole percent. Spec §3.1.
EOF
)"
```

---

## Task 6: Rewrite `FamilyDistribution` (Top-N + Other + searchable list)

**Files:**

- Modify: `frontend/src/components/charts/FamilyDistribution.tsx` (full rewrite)
- Test: `frontend/tests/unit/components/charts/FamilyDistribution.test.tsx` (new)

- [ ] **Step 1: Replace the contents of `frontend/src/components/charts/FamilyDistribution.tsx`**

```tsx
import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  LabelList,
} from "recharts";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { aggregateLongTail } from "./FamilyDistribution.logic";

const PRIMARY = "hsl(var(--primary))";
const MUTED = "hsl(var(--muted-foreground))";

interface Props {
  data: Record<string, number>;
}

type SortKey = "count" | "name";

export function FamilyDistribution({ data }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("count");

  const totalFamilies = Object.keys(data).length;
  const totalSamples = useMemo(
    () => Object.values(data).reduce((a, b) => a + b, 0),
    [data],
  );
  const bars = useMemo(() => aggregateLongTail(data, 10), [data]);

  const allRows = useMemo(() => {
    const rows = Object.entries(data)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .map((r, i) => ({ ...r, rank: i + 1 }));
    return rows;
  }, [data]);

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? allRows.filter((r) => r.name.toLowerCase().includes(q))
      : allRows;
    if (sortKey === "name") {
      return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
    }
    return filtered; // already count-desc
  }, [allRows, query, sortKey]);

  if (totalFamilies === 0)
    return (
      <p className="text-muted-foreground">
        {t("datasets.detail.noFamilyData")}
      </p>
    );

  const containerHeight = Math.min((bars.length + 1) * 36, 360);

  return (
    <div className="space-y-3">
      {totalFamilies > bars.filter((b) => !b.isOther).length && (
        <p className="text-xs text-muted-foreground">
          {t("datasets.detail.topOf", {
            shown: bars.filter((b) => !b.isOther).length,
            total: totalFamilies,
          })}
        </p>
      )}
      <div style={{ width: "100%", height: containerHeight }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={bars}
            layout="vertical"
            margin={{ left: 12, right: 56, top: 4, bottom: 4 }}
            barCategoryGap={4}
          >
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={120}
              interval={0}
              tick={{ fontSize: 12 }}
            />
            <Tooltip
              cursor={{ fill: "hsl(var(--muted) / 0.3)" }}
              formatter={(value: number) => [
                `${value} (${((value / totalSamples) * 100).toFixed(1)}%)`,
                "",
              ]}
            />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {bars.map((b) => (
                <Cell key={b.name} fill={b.isOther ? MUTED : PRIMARY} />
              ))}
              <LabelList
                dataKey="value"
                position="right"
                formatter={(value: number) =>
                  `${value} (${((value / totalSamples) * 100).toFixed(1)}%)`
                }
                style={{
                  fill: "hsl(var(--foreground))",
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {t("datasets.detail.showAllFamilies", { n: totalFamilies })}
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 space-y-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("datasets.detail.searchFamilies")}
          />
          <div className="overflow-x-auto rounded border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">
                    <button
                      type="button"
                      onClick={() => setSortKey("name")}
                      className="hover:text-foreground"
                    >
                      {t("datasets.detail.tableFamily")}
                    </button>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => setSortKey("count")}
                      className="hover:text-foreground"
                    >
                      {t("datasets.detail.tableCount")}
                    </button>
                  </th>
                  <th className="px-3 py-2 text-right">
                    {t("datasets.detail.tablePercent")}
                  </th>
                  <th className="px-3 py-2 text-right">
                    {t("datasets.detail.tableRank")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((r) => (
                  <tr key={r.name} className="border-t">
                    <td className="px-3 py-1.5 font-mono text-xs">{r.name}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.value.toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {((r.value / totalSamples) * 100).toFixed(1)}%
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.rank}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
```

- [ ] **Step 2: Add component test at `frontend/tests/unit/components/charts/FamilyDistribution.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FamilyDistribution } from "@/components/charts/FamilyDistribution";

describe("<FamilyDistribution>", () => {
  it("renders empty state when data is empty", () => {
    render(<FamilyDistribution data={{}} />);
    expect(screen.getByText("No family data.")).toBeInTheDocument();
  });

  it("renders Top-N suffix only when families exceed topN", () => {
    const small = Object.fromEntries(
      Array.from({ length: 5 }, (_, i) => [`f${i}`, i + 1]),
    );
    const { rerender } = render(<FamilyDistribution data={small} />);
    expect(screen.queryByText(/Showing top/)).toBeNull();

    const big = Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => [`f${i}`, 12 - i]),
    );
    rerender(<FamilyDistribution data={big} />);
    expect(screen.getByText(/Showing top 10 of 12/)).toBeInTheDocument();
  });

  it("collapsed list renders all rows when expanded", async () => {
    const data = Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => [`fam${i}`, 12 - i]),
    );
    render(<FamilyDistribution data={data} />);
    const trigger = screen.getByRole("button", {
      name: /Show all 12 families/,
    });
    await userEvent.click(trigger);
    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(13); // header + 12
  });

  it("search filters table case-insensitively", async () => {
    const data = { mirai: 50, dridex: 30, wannacry: 10 };
    render(<FamilyDistribution data={data} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Show all 3 families/ }),
    );
    const search = screen.getByPlaceholderText(/Search families/);
    await userEvent.type(search, "MiR");
    const table = await screen.findByRole("table");
    expect(within(table).getByText("mirai")).toBeInTheDocument();
    expect(within(table).queryByText("dridex")).toBeNull();
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend && pnpm test -- FamilyDistribution.test`
Expected: PASS, all 4 cases green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/charts/FamilyDistribution.tsx frontend/tests/unit/components/charts/FamilyDistribution.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): redesign FamilyDistribution with Top-N + Other + table

Bar chart now displays Top 10 + Other(N) aggregate, with count and
share rendered inline at the right edge of each bar. A collapsible
searchable / sortable table beneath surfaces the full family list.
Spec §3.2.
EOF
)"
```

---

## Task 7: Add dataset detail subcomponents (Header, KPI strip, Metadata details)

Three small co-located presentation components live under `src/components/datasets/`. The folder is new.

**Files:**

- Create: `frontend/src/components/datasets/DatasetHeader.tsx`
- Create: `frontend/src/components/datasets/DatasetKpiStrip.tsx`
- Create: `frontend/src/components/datasets/DatasetMetadataDetails.tsx`
- Test: `frontend/tests/unit/components/datasets/DatasetKpiStrip.test.tsx`

- [ ] **Step 1: Create `frontend/src/components/datasets/DatasetHeader.tsx`**

```tsx
import { useNavigate } from "react-router";
import { useDeleteDataset, type Dataset } from "@/api/queries/datasets";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface Props {
  dataset: Dataset;
}

export function DatasetHeader({ dataset }: Props) {
  const nav = useNavigate();
  const del = useDeleteDataset();
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold leading-tight">
            {dataset.name}
          </h1>
          <Badge variant="outline" className="capitalize">
            {dataset.visibility}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {dataset.description ?? "—"}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <a
          href={`/api/v1/datasets/${dataset.id}/csv`}
          className="inline-flex h-9 items-center rounded-md border bg-background px-3 text-sm hover:bg-accent"
        >
          Download CSV
        </a>
        <Button
          variant="destructive"
          size="sm"
          onClick={async () => {
            if (!confirm("Delete this dataset?")) return;
            await del.mutateAsync(dataset.id);
            nav("/datasets");
          }}
        >
          Delete
        </Button>
      </div>
    </header>
  );
}
```

> Note: `Dataset` type is whatever `useDataset` already returns. If `frontend/src/api/queries/datasets.ts` does not export a `Dataset` alias, add one as the first thing inside this task — single line: `export type Dataset = components["schemas"]["DatasetConfigRead"];`. Place it next to the existing exports following the precedent of `RegisteredModel` in `models.ts:5`.

- [ ] **Step 2: Create `frontend/src/components/datasets/DatasetKpiStrip.tsx`**

```tsx
import { useTranslation } from "react-i18next";
import type { Dataset } from "@/api/queries/datasets";
import { Card, CardContent } from "@/components/ui/card";
import { formatRelative } from "@/lib/date";

interface Props {
  dataset: Dataset;
}

interface Tile {
  label: string;
  value: string;
  numberClass?: string;
}

export function DatasetKpiStrip({ dataset }: Props) {
  const { t } = useTranslation();
  const labels = (dataset.label_distribution ?? {}) as Record<string, number>;
  const families = (dataset.family_distribution ?? {}) as Record<
    string,
    number
  >;

  const tiles: Tile[] = [
    {
      label: t("datasets.detail.kpi.samples"),
      value: dataset.sample_count.toLocaleString(),
    },
    {
      label: t("datasets.detail.kpi.malware"),
      value: (labels["Malware"] ?? 0).toLocaleString(),
      numberClass: "text-red-600 dark:text-red-500",
    },
    {
      label: t("datasets.detail.kpi.benign"),
      value: (labels["Benign"] ?? 0).toLocaleString(),
      numberClass: "text-green-600 dark:text-green-500",
    },
    {
      label: t("datasets.detail.kpi.families"),
      value: Object.keys(families).length.toLocaleString(),
    },
    {
      label: t("datasets.detail.kpi.created"),
      value: formatRelative(dataset.created_at),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
      {tiles.map((tile) => (
        <Card key={tile.label}>
          <CardContent className="px-4 py-3">
            <div
              className={`text-2xl font-semibold tabular-nums leading-tight ${tile.numberClass ?? ""}`}
            >
              {tile.value}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {tile.label}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/datasets/DatasetMetadataDetails.tsx`**

```tsx
import { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Dataset } from "@/api/queries/datasets";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";

interface Props {
  dataset: Dataset;
}

export function DatasetMetadataDetails({ dataset }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {t("datasets.detail.metadata")}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2 rounded border p-3 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("datasets.detail.owner")}:
          </span>
          <code className="font-mono text-xs">{dataset.owner_id}</code>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("datasets.detail.checksum")}:
          </span>
          <code className="break-all font-mono text-xs">
            {dataset.csv_checksum}
          </code>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2"
            onClick={async () => {
              await navigator.clipboard.writeText(dataset.csv_checksum);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? t("common.copied") : t("common.copy")}
          </Button>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
```

- [ ] **Step 4: Add component test at `frontend/tests/unit/components/datasets/DatasetKpiStrip.test.tsx`**

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DatasetKpiStrip } from "@/components/datasets/DatasetKpiStrip";
import type { Dataset } from "@/api/queries/datasets";

const baseDataset = {
  id: "00000000-0000-0000-0000-000000000000",
  name: "ds",
  description: null,
  owner_id: "11111111-1111-1111-1111-111111111111",
  visibility: "public",
  sample_count: 1548,
  label_distribution: { Malware: 912, Benign: 636 },
  family_distribution: { mirai: 234, dridex: 187 },
  size_bytes: 4096,
  csv_checksum: "deadbeef",
  created_at: new Date(Date.now() - 60_000).toISOString(),
} as unknown as Dataset;

describe("<DatasetKpiStrip>", () => {
  it("renders five tiles with formatted numbers", () => {
    render(<DatasetKpiStrip dataset={baseDataset} />);
    expect(screen.getByText("1,548")).toBeInTheDocument();
    expect(screen.getByText("912")).toBeInTheDocument();
    expect(screen.getByText("636")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // families
    expect(screen.getByText(/seconds ago|minute/i)).toBeInTheDocument();
  });

  it("falls back to 0 when label/family distributions are missing", () => {
    const dataset = {
      ...baseDataset,
      label_distribution: {},
      family_distribution: null,
    } as unknown as Dataset;
    render(<DatasetKpiStrip dataset={dataset} />);
    const zeros = screen.getAllByText("0");
    // Malware = 0, Benign = 0, Families = 0
    expect(zeros.length).toBeGreaterThanOrEqual(3);
  });
});
```

- [ ] **Step 5: If `Dataset` type alias is missing, export it from `frontend/src/api/queries/datasets.ts`**

Run: `grep -n "export type Dataset " frontend/src/api/queries/datasets.ts`
If empty, add this line near the other type exports at the top:

```typescript
import type { components } from "@/api/schema.gen";
export type Dataset = components["schemas"]["DatasetConfigRead"];
```

- [ ] **Step 6: Run the tests**

Run: `cd frontend && pnpm test -- DatasetKpiStrip.test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/datasets/ frontend/src/api/queries/datasets.ts frontend/tests/unit/components/datasets/DatasetKpiStrip.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): add dataset detail subcomponents

DatasetHeader (title + badge + actions), DatasetKpiStrip (5-tile
stat row with semantic colors for Malware/Benign), and
DatasetMetadataDetails (collapsible owner + checksum + copy
button). Exports a `Dataset` type alias from datasets queries to
match RegisteredModel precedent. Spec §2.2–2.4.
EOF
)"
```

---

## Task 8: Rewrite the dataset detail route

Wires the new subcomponents and the rewritten charts together. The route file becomes a thin assembler.

**Files:**

- Modify: `frontend/src/routes/_authed.datasets.$id.tsx` (full rewrite)

- [ ] **Step 1: Replace the contents of `frontend/src/routes/_authed.datasets.$id.tsx`**

```tsx
import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useDataset } from "@/api/queries/datasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LabelDistribution } from "@/components/charts/LabelDistribution";
import { FamilyDistribution } from "@/components/charts/FamilyDistribution";
import { DatasetHeader } from "@/components/datasets/DatasetHeader";
import { DatasetKpiStrip } from "@/components/datasets/DatasetKpiStrip";
import { DatasetMetadataDetails } from "@/components/datasets/DatasetMetadataDetails";

export const handle = { breadcrumb: "Dataset" };

export default function DatasetDetailPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { data } = useDataset(id);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;

  const labelDist = (data.label_distribution ?? {}) as Record<string, number>;
  const familyDist = (data.family_distribution ?? {}) as Record<string, number>;

  return (
    <div className="space-y-4">
      <DatasetHeader dataset={data} />
      <DatasetKpiStrip dataset={data} />
      <Card>
        <CardHeader>
          <CardTitle>{t("datasets.detail.labelDistribution")}</CardTitle>
        </CardHeader>
        <CardContent>
          <LabelDistribution data={labelDist} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("datasets.detail.familyDistribution")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FamilyDistribution data={familyDist} />
        </CardContent>
      </Card>
      <DatasetMetadataDetails dataset={data} />
    </div>
  );
}
```

- [ ] **Step 2: Verify type / lint / format**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check`
Expected: PASS.

- [ ] **Step 3: Run the full unit suite**

Run: `cd frontend && pnpm test`
Expected: PASS — existing tests touching this route should keep passing because we did not change props or query keys.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/_authed.datasets.$id.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): rewrite dataset detail layout

Route file now assembles DatasetHeader / KpiStrip / charts /
MetadataDetails top-to-bottom. Removes the misleading Size field,
moves checksum into the collapsible Metadata block, and gives the
family chart a full-width row. Spec §2.1.
EOF
)"
```

---

## Task 9: Modernize `DatasetUploadForm`

Five fixes in one component edit. Each step is small but they share the same file, so they ship together.

**Files:**

- Modify: `frontend/src/components/forms/DatasetUploadForm.tsx`
- Modify: `frontend/tests/unit/components/DatasetUploadForm.test.tsx`

- [ ] **Step 1: Replace the file at `frontend/src/components/forms/DatasetUploadForm.tsx`**

```tsx
import { type ChangeEvent, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateDataset } from "@/api/queries/datasets";
import { parseCsvPreview, type CsvPreview } from "@/lib/csv";
import { checkCsvSize } from "./DatasetUploadForm.logic";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";
import { StickyFormFooter } from "./StickyFormFooter";

const schema = z.object({
  name: z.string().min(1).max(100),
  description: z.string().optional(),
  visibility: z.enum(["public", "private"]),
  csv_content: z.string().min(1, "CSV content is required"),
});
type Values = z.infer<typeof schema>;

export function DatasetUploadForm() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const mut = useCreateDataset();
  const {
    register,
    handleSubmit,
    setValue,
    setError,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { visibility: "public" },
  });
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const content = watch("csv_content");
  const visibility = watch("visibility");

  async function onFilePick(ev: ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setValue("csv_content", text, { shouldValidate: true });
    runPreview(text);
  }

  function runPreview(text: string) {
    setParseError(null);
    const sizeErr = checkCsvSize(text);
    if (sizeErr) {
      setParseError(sizeErr);
      setPreview(null);
      return;
    }
    try {
      setPreview(parseCsvPreview(text, 10));
    } catch (e) {
      setParseError((e as Error).message);
      setPreview(null);
    }
  }

  const onSubmit = handleSubmit(async (v) => {
    const sizeErr = checkCsvSize(v.csv_content);
    if (sizeErr) {
      setError("csv_content", { message: sizeErr });
      return;
    }
    try {
      parseCsvPreview(v.csv_content, 1); // surface row-level errors before POST
    } catch (e) {
      setError("csv_content", { message: (e as Error).message });
      return;
    }
    try {
      const ds = await mut.mutateAsync(v);
      nav(`/datasets/${ds.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });

  return (
    <form className="max-w-2xl space-y-4" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          placeholder={t("datasets.new.namePlaceholder")}
          {...register("name")}
        />
        {errors.name && (
          <p className="text-xs text-destructive">{errors.name.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" rows={2} {...register("description")} />
      </div>
      <div>
        <Label htmlFor="visibility">Visibility</Label>
        <Select
          value={visibility}
          onValueChange={(v) =>
            setValue("visibility", v as "public" | "private", {
              shouldValidate: true,
            })
          }
        >
          <SelectTrigger id="visibility">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="public">Public (all lab members)</SelectItem>
            <SelectItem value="private">Private (me + admin)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>CSV content</Label>
        <Tabs defaultValue="file">
          <TabsList>
            <TabsTrigger value="file">File picker</TabsTrigger>
            <TabsTrigger value="paste">Paste</TabsTrigger>
          </TabsList>
          <TabsContent value="file">
            <Input type="file" accept=".csv,text/csv" onChange={onFilePick} />
          </TabsContent>
          <TabsContent value="paste">
            <Textarea
              rows={8}
              placeholder={"file_name,label,family\nabc…,Malware,mirai"}
              value={content ?? ""}
              onChange={(e) => {
                setValue("csv_content", e.target.value);
                runPreview(e.target.value);
              }}
            />
          </TabsContent>
        </Tabs>
        {errors.csv_content && (
          <p className="text-xs text-destructive">
            {errors.csv_content.message}
          </p>
        )}
        {parseError && (
          <Alert variant="destructive">
            <AlertDescription>{parseError}</AlertDescription>
          </Alert>
        )}
        {preview && (
          <div className="rounded border p-2 text-xs">
            <p className="mb-1 text-muted-foreground">
              Preview ({preview.rows.length} of {preview.totalRows} rows)
            </p>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    {preview.columns.map((c) => (
                      <th key={c} className="text-left">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => (
                    <tr key={i}>
                      {preview.columns.map((c) => (
                        <td key={c} className="truncate">
                          {r[c]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <StickyFormFooter>
        <Button
          type="button"
          variant="ghost"
          className="h-11"
          onClick={() => nav(-1)}
        >
          {t("common.cancel")}
        </Button>
        <Button
          type="submit"
          disabled={isSubmitting || !!parseError}
          className="h-11"
        >
          {isSubmitting ? t("datasets.new.submitting") : "Upload dataset"}
        </Button>
      </StickyFormFooter>
    </form>
  );
}
```

- [ ] **Step 2: Extend `frontend/tests/unit/components/DatasetUploadForm.test.tsx`**

Replace the file with:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  checkCsvSize,
  MAX_CSV_BYTES,
} from "@/components/forms/DatasetUploadForm.logic";
import { DatasetUploadForm } from "@/components/forms/DatasetUploadForm";

function renderForm() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <DatasetUploadForm />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("checkCsvSize", () => {
  it("accepts small CSV", () => {
    expect(checkCsvSize("a,b\n1,2\n")).toBeNull();
  });
  it("rejects > 10 MB", () => {
    const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
    expect(checkCsvSize(oversize)).toMatch(/exceeds/i);
  });
});

describe("<DatasetUploadForm>", () => {
  it("uses the i18n placeholder for the Name input", () => {
    renderForm();
    expect(
      screen.getByPlaceholderText(/malware-train-2026-q1/),
    ).toBeInTheDocument();
  });

  it("renders a Cancel button alongside Submit", () => {
    renderForm();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Upload dataset/ }),
    ).toBeInTheDocument();
  });

  it("renders a shadcn Visibility Select (combobox role)", () => {
    renderForm();
    // shadcn Select renders an accessible combobox button (Radix Select.Trigger).
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("blocks submit when CSV row validation fails", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText("Name"), "ds");
    // Switch to Paste tab and type an obviously bad CSV
    await user.click(screen.getByRole("tab", { name: /Paste/ }));
    const textarea = screen.getByPlaceholderText(/file_name,label,family/);
    await user.type(textarea, "file_name,label\nDEADBEEF,Malware\n");
    // Inline preview parser should already surface the SHA256 error via Alert
    expect(await screen.findByText(/SHA256/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend && pnpm test -- DatasetUploadForm.test`
Expected: PASS, 6 cases green (2 logic + 4 component).

- [ ] **Step 4: Verify type / lint / format**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/DatasetUploadForm.tsx frontend/tests/unit/components/DatasetUploadForm.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): modernize DatasetUploadForm UX

Replaces the native <select> with shadcn Select (now respects dark
mode), adds a Cancel button to the sticky footer, swaps the stale
demo placeholder for an i18n key, and runs CSV row-level
validation on submit so users see SHA256 / label / family errors
before a 422 round trip. Spec §4.
EOF
)"
```

---

## Task 10: Add MLflow stage explainer to the models list

**Files:**

- Modify: `frontend/src/routes/_authed.models._index.tsx`
- Test: `frontend/tests/unit/components/ModelsListExplainer.test.tsx` (new)

- [ ] **Step 1: Replace the contents of `frontend/src/routes/_authed.models._index.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { HelpCircle, X } from "lucide-react";
import {
  useRegisteredModels,
  type RegisteredModel,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Models" };

const DISMISSED_KEY = "lolday.modelsExplainerDismissed";

function HeaderWithTooltip({ label, hint }: { label: string; hint: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground"
              aria-label={`${label} info`}
            >
              <HelpCircle size={14} />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">{hint}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </span>
  );
}

function buildColumns(t: (k: string) => string): ColumnDef<RegisteredModel>[] {
  return [
    {
      accessorKey: "name",
      header: "Name",
      cell: ({ row }) => (
        <Link
          to={`/models/${encodeURIComponent(row.original.name)}`}
          className="font-medium hover:underline"
        >
          {row.original.name}
        </Link>
      ),
      meta: { cardSlot: "title" },
    },
    {
      accessorKey: "latest_version",
      header: "Latest version",
      meta: { cardLabel: "Latest", cardSlot: "body" },
    },
    {
      id: "staging",
      header: () => (
        <HeaderWithTooltip
          label="Staging"
          hint={t("models.stages.stagingTooltip")}
        />
      ),
      cell: ({ row }) =>
        row.original.latest_staging_version != null ? (
          <Badge variant="secondary">
            v{row.original.latest_staging_version}
          </Badge>
        ) : (
          <span className="text-muted-foreground">
            {t("models.notPromoted")}
          </span>
        ),
      meta: { cardLabel: "Staging", cardSlot: "body" },
    },
    {
      id: "prod",
      header: () => (
        <HeaderWithTooltip
          label="Production"
          hint={t("models.stages.productionTooltip")}
        />
      ),
      cell: ({ row }) =>
        row.original.latest_production_version != null ? (
          <Badge className="bg-emerald-600">
            v{row.original.latest_production_version}
          </Badge>
        ) : (
          <span className="text-muted-foreground">
            {t("models.notPromoted")}
          </span>
        ),
      meta: { cardLabel: "Production", cardSlot: "body" },
    },
  ];
}

function StageExplainerAlert() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(true); // start hidden to avoid flash

  useEffect(() => {
    setDismissed(localStorage.getItem(DISMISSED_KEY) === "1");
  }, []);

  if (dismissed) return null;
  return (
    <Alert className="relative">
      <AlertTitle>{t("models.stagesExplainer.title")}</AlertTitle>
      <AlertDescription className="pr-8">
        {t("models.stagesExplainer.body")}
      </AlertDescription>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="absolute right-2 top-2 h-7 w-7 p-0"
        aria-label={t("common.dismiss")}
        onClick={() => {
          localStorage.setItem(DISMISSED_KEY, "1");
          setDismissed(true);
        }}
      >
        <X size={14} />
      </Button>
    </Alert>
  );
}

export default function ModelsListPage() {
  const { t } = useTranslation();
  const { data, isLoading } = useRegisteredModels();
  const columns = buildColumns(t);
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <PageHeader title="Models" />
      <StageExplainerAlert />
      <DataTable
        data={data ?? []}
        columns={columns}
        emptyMessage="No models registered yet."
      />
    </div>
  );
}
```

- [ ] **Step 2: Add component test at `frontend/tests/unit/components/ModelsListExplainer.test.tsx`**

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ModelsListPage from "@/routes/_authed.models._index";

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // Pre-seed an empty list so the table renders immediately
  qc.setQueryData(["models", "list"], []);
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ModelsListPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ModelsListPage stage explainer", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the explainer Alert on first visit", () => {
    renderPage();
    expect(screen.getByText(/About model stages/)).toBeInTheDocument();
  });

  it("hides the explainer after Dismiss is clicked", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    expect(screen.queryByText(/About model stages/)).toBeNull();
    expect(localStorage.getItem("lolday.modelsExplainerDismissed")).toBe("1");
  });

  it("does not render the explainer when localStorage flag is set", () => {
    localStorage.setItem("lolday.modelsExplainerDismissed", "1");
    renderPage();
    expect(screen.queryByText(/About model stages/)).toBeNull();
  });
});
```

- [ ] **Step 3: Run the tests**

Run: `cd frontend && pnpm test -- ModelsListExplainer.test`
Expected: PASS, 3 cases green.

- [ ] **Step 4: Verify type / lint / format**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.models._index.tsx frontend/tests/unit/components/ModelsListExplainer.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): add MLflow stage explainer to models list

Adds a dismissible Alert above the models table explaining the
None / Staging / Production / Archived lifecycle, header tooltips
on the Staging and Production columns, and replaces empty cells
with `Not promoted` (muted). Dismiss state persists in
localStorage. Spec §5.
EOF
)"
```

---

## Task 11: Verification gate

End-to-end sanity. Catch anything the unit suite missed before declaring done.

**Files:** none modified.

- [ ] **Step 1: Run the full unit suite**

Run: `cd frontend && pnpm test`
Expected: PASS — all suites green, no skipped tests beyond pre-existing skips.

- [ ] **Step 2: Run typecheck, lint, format check**

Run: `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check`
Expected: PASS for all three.

- [ ] **Step 3: Run pre-commit one final time across the repo**

Run: `pre-commit run --all-files`
Expected: all hooks PASS.

- [ ] **Step 4: Manual smoke against `pnpm dev`**

Per `.claude/rules/frontend.md`: type checking validates code, not feature correctness. Bring up the dev server and walk the redesigned surfaces.

```bash
cd frontend && pnpm dev
```

Open `http://localhost:5173` and verify:

1. `/datasets/<id>` (any existing dataset)
   - KPI strip shows 5 tiles with `tabular-nums`; Mal/Ben values are red / green respectively
   - Label distribution donut: Malware slice is `#dc2626`, Benign slice is `#16a34a`; donut center shows the dominant percent
   - Family chart: Top 10 + Other(N) row when families > 10; each bar has count + share at the right edge
   - "Show all N families" expands a searchable table; case-insensitive substring filter works
   - "Metadata" collapsible reveals checksum + Copy button; Copy toggles to "Copied" briefly
   - **No "Size" anywhere on the page**
2. `/datasets/new`
   - Name placeholder reads `e.g. malware-train-2026-q1` (or zh-TW 對應字串)
   - Visibility shows shadcn Select; toggling between dark / light theme keeps trigger and dropdown styled correctly
   - Pasting CSV with a non-SHA256 file_name surfaces a row-N error in the destructive Alert
   - Cancel button returns to the previous page (`/datasets` if entered from there)
3. `/models`
   - Stage explainer Alert renders on first visit
   - Clicking Dismiss hides it; reload keeps it dismissed
   - Tooltip on the `?` icon next to `Staging` and `Production` shows the helper copy
   - Empty cells read `Not promoted` (or zh-TW)

If any visual issue appears, file a follow-up — do not patch silently.

- [ ] **Step 5: Production CSP smoke** (per `frontend.md` rule: dev-server pass ≠ built-image pass)

```bash
cd frontend && pnpm build && pnpm preview
```

Open the preview URL and reproduce the smoke walkthrough from Step 4 with DevTools console open. Expected: zero CSP violations. If a violation surfaces (typically Recharts inline styles vs. `script-src 'self'`), capture it and stop — that is a release blocker.

- [ ] **Step 6: Sign off**

If every step above is green, the spec's done criteria (§11) are satisfied. The branch is ready for PR via `commit-commands:commit-push-pr` or equivalent.

---

## Done criteria recap (from spec §11)

- [x] `/datasets/:id` renders sectioned layout with semantic-color label donut and Top 10 + Other family chart + collapsible searchable table.
- [x] `/datasets/new` has Cancel button, shadcn Visibility Select, new placeholder, frontend CSV row validation.
- [x] `/models` shows dismissible explainer + tooltip headers + `Not promoted` empty cells.
- [x] Existing tests still pass; new tests cover the changes (§7 in spec).
- [x] `pnpm typecheck && pnpm lint && pnpm format:check && pnpm test` green from `frontend/`.
- [x] Built nginx image renders the same pages with no CSP violations in DevTools.
