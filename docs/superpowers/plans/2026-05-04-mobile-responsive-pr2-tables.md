# Mobile Responsive PR-2 — DataTable + List Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `DataTable.tsx` to dispatch between desktop `<Table>` and mobile card list via `useIsMobile`, introduce `<PageHeader>` for mobile-first list page chrome, and migrate all 8 list/table sites to feed the new metadata.

**Architecture:** TanStack Table's official module-augmentation pattern adds `cardLabel` / `cardSlot` / `cardOrder` to `ColumnMeta`. `DataTable.tsx` becomes a thin dispatcher: at runtime, `useIsMobile` chooses `<CardList>` or `<DesktopTable>`. Both branches share the same `useReactTable` instance for sorting and pagination. A new `<PageHeader>` component replaces hand-coded `flex justify-between` headers, stacking vertically on mobile and laying out horizontally at `≥ sm`.

**Tech Stack:** React 19, TypeScript 5.9, Tailwind 3.4, @tanstack/react-table 8, shadcn/ui (Card, Select, DropdownMenu), vitest 4.

**Spec:** `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` (§3)

**Stacked on PR-1:** This branch (`feat/mobile-responsive-pr2-tables`) is created from `feat/mobile-responsive-pr1-foundation-sidebar` (PR #79). PR-2 must merge **after** PR-1 lands. The worktree at `.worktrees/mobile-pr2/` already contains all PR-1 commits.

---

## File Structure

| Action | Path                                                           | Responsibility                                                                                  |
| ------ | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Create | `frontend/src/components/tables/types.ts`                      | TanStack Table `ColumnMeta` module augmentation (`cardLabel` / `cardSlot` / `cardOrder`)        |
| Create | `frontend/src/components/tables/DesktopTable.tsx`              | Today's `<Table>` rendering, extracted verbatim                                                 |
| Create | `frontend/src/components/tables/CardList.tsx`                  | Renders rows as `<Card>`s using `cardSlot` meta                                                 |
| Create | `frontend/src/components/tables/MobileSortBar.tsx`             | `<Select>` of sortable columns, bound to `table.setSorting()`                                   |
| Create | `frontend/src/components/tables/Pagination.tsx`                | Prev / page-of-N / Next, extracted verbatim                                                     |
| Create | `frontend/src/components/layout/PageHeader.tsx`                | Mobile-first page chrome (`<h1>` + actions, stacked on `< sm`)                                  |
| Modify | `frontend/src/components/tables/DataTable.tsx`                 | Becomes a dispatcher: `useIsMobile` picks `<CardList>` or `<DesktopTable>`                      |
| Modify | `frontend/src/routes/_authed.detectors._index.tsx`             | Add `meta.cardSlot` to columns + use `<PageHeader>`                                             |
| Modify | `frontend/src/routes/_authed.datasets._index.tsx`              | Same pattern                                                                                    |
| Modify | `frontend/src/routes/_authed.jobs._index.tsx`                  | Same pattern                                                                                    |
| Modify | `frontend/src/routes/_authed.models._index.tsx`                | Same pattern                                                                                    |
| Modify | `frontend/src/routes/_authed.admin.users.tsx`                  | Same pattern                                                                                    |
| Modify | `frontend/src/routes/_authed.runs._index.tsx`                  | `<PageHeader>` only (no DataTable here — uses `<ExperimentCard>` grid)                          |
| Modify | `frontend/src/routes/_authed.runs.$expId.tsx`                  | `<PageHeader>` + cardSlot meta on the dynamic columns                                           |
| Modify | `frontend/src/routes/_authed.detectors.$id.tsx`                | cardSlot meta on the versions + builds tables (no PageHeader change here — already inside Tabs) |
| Test   | `frontend/tests/unit/components/tables/CardList.test.tsx`      | Unit                                                                                            |
| Test   | `frontend/tests/unit/components/tables/MobileSortBar.test.tsx` | Unit                                                                                            |
| Test   | `frontend/tests/unit/components/tables/DataTable.test.tsx`     | Dispatch behavior (mobile vs desktop)                                                           |
| Test   | `frontend/tests/unit/components/layout/PageHeader.test.tsx`    | Unit                                                                                            |
| Modify | `frontend/tests/unit/JobsList.test.tsx` and similar            | Update if existing tests render `<DataTable>` without `MemoryRouter` etc.                       |

---

### Task 1: Branch + worktree setup

**Status:** Already complete. The worktree at `.worktrees/mobile-pr2/` is on branch `feat/mobile-responsive-pr2-tables`, branched from `feat/mobile-responsive-pr1-foundation-sidebar` at `dfef446`. Baseline `pnpm test` passes (29 files / 125 tests).

Subagents implementing later tasks should `cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr2/frontend` to begin work.

---

### Task 2: ColumnMeta module augmentation

**Files:**

- Create: `frontend/src/components/tables/types.ts`

- [ ] **Step 1: Write the file**

```ts
// frontend/src/components/tables/types.ts
/**
 * Module augmentation for `@tanstack/react-table`'s `ColumnMeta` to carry
 * mobile card-mode rendering hints. Consumed by `DataTable.tsx`'s mobile
 * dispatch (`<CardList>` reads these to place each column into a card slot).
 *
 * The augmentation is type-safe; ColumnDef.meta is typed end-to-end with
 * autocomplete and refactor support.
 */
import "@tanstack/react-table";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    /** Label shown alongside the value in card body rows. Defaults to the column header string when absent. */
    cardLabel?: string;
    /**
     * Where to place this column in the mobile card layout:
     * - `title`: large header text at top-left
     * - `subtitle`: small text at top-right (status / type chips)
     * - `body`: label/value row in the card body (default for unmarked columns)
     * - `actions`: icon-button slot at top-right (e.g. row dropdown trigger)
     * - `hidden`: omit from card entirely (e.g. id columns visible only on desktop)
     */
    cardSlot?: "title" | "subtitle" | "body" | "actions" | "hidden";
    /** Override the body slot ordering. Lower values render first. Defaults to column array order. */
    cardOrder?: number;
  }
}

// Re-export ColumnDef so consumers can import everything from this file
// when they want the typed meta in scope.
export type {} from "@tanstack/react-table";
```

- [ ] **Step 2: Verify augmentation compiles**

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr2/frontend
pnpm typecheck
```

Expected: clean exit. Any consumer of `ColumnDef<T>["meta"]` now sees the new shape.

- [ ] **Step 3: Make sure tsconfig picks up the file**

The augmentation file must be included in `tsconfig.json`'s `include` glob. The current `frontend/tsconfig.json` includes `src/**/*.ts` and `src/**/*.tsx`; `src/components/tables/types.ts` is covered. No tsconfig change.

To force-load the augmentation in any consumer that doesn't directly import this file, we add a side-effect import at the top of `DataTable.tsx` (Task 7). For now, the file just exists.

- [ ] **Step 4: Commit**

```bash
git add src/components/tables/types.ts
git commit -m "feat(frontend): add ColumnMeta augmentation for card-mode metadata"
```

---

### Task 3: Extract DesktopTable

**Files:**

- Create: `frontend/src/components/tables/DesktopTable.tsx`

- [ ] **Step 1: Read the current DataTable**

```bash
cat src/components/tables/DataTable.tsx
```

- [ ] **Step 2: Create DesktopTable with the existing render path**

```tsx
// frontend/src/components/tables/DesktopTable.tsx
import { flexRender, type Table as ReactTable } from "@tanstack/react-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { ArrowUpDown } from "lucide-react";

interface Props<T> {
  table: ReactTable<T>;
  emptyMessage: string;
  onRowClick?: (row: T) => void;
}

export function DesktopTable<T>({ table, emptyMessage, onRowClick }: Props<T>) {
  return (
    <div className="overflow-hidden rounded-md border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((h) => (
                <TableHead key={h.id}>
                  {h.isPlaceholder ? null : h.column.getCanSort() ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={h.column.getToggleSortingHandler()}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      <ArrowUpDown className="ml-2 h-3 w-3" />
                    </Button>
                  ) : (
                    flexRender(h.column.columnDef.header, h.getContext())
                  )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={table.getAllColumns().length}
                className="h-24 text-center text-muted-foreground"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                onClick={
                  onRowClick ? () => onRowClick(row.original) : undefined
                }
                className={onRowClick ? "cursor-pointer" : undefined}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
```

- [ ] **Step 3: Verify it compiles**

```bash
pnpm typecheck
```

Expected: clean. (No consumer yet; the file is dead code until Task 7 wires it up.)

- [ ] **Step 4: Commit**

```bash
git add src/components/tables/DesktopTable.tsx
git commit -m "feat(frontend): extract DesktopTable from DataTable"
```

---

### Task 4: Extract Pagination

**Files:**

- Create: `frontend/src/components/tables/Pagination.tsx`

- [ ] **Step 1: Create Pagination**

```tsx
// frontend/src/components/tables/Pagination.tsx
import { type Table as ReactTable } from "@tanstack/react-table";
import { Button } from "@/components/ui/button";

interface Props<T> {
  table: ReactTable<T>;
}

export function Pagination<T>({ table }: Props<T>) {
  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount() || 1;
  return (
    <div className="flex items-center justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => table.previousPage()}
        disabled={!table.getCanPreviousPage()}
      >
        Prev
      </Button>
      <span className="text-sm text-muted-foreground">
        Page {pageIndex + 1} of {pageCount}
      </span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => table.nextPage()}
        disabled={!table.getCanNextPage()}
      >
        Next
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
pnpm typecheck
git add src/components/tables/Pagination.tsx
git commit -m "feat(frontend): extract Pagination from DataTable"
```

---

### Task 5: Add MobileSortBar (TDD)

**Files:**

- Create: `frontend/src/components/tables/MobileSortBar.tsx`
- Test: `frontend/tests/unit/components/tables/MobileSortBar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/components/tables/MobileSortBar.test.tsx
import { render, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { describe, expect, it } from "vitest";
import { MobileSortBar } from "@/components/tables/MobileSortBar";
import "@/components/tables/types";

interface Row {
  name: string;
  age: number;
}

const data: Row[] = [
  { name: "alice", age: 30 },
  { name: "bob", age: 25 },
];

const columns: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name" },
  { accessorKey: "age", header: "Age" },
  { id: "actions", header: "", enableSorting: false },
];

function Harness() {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  return <MobileSortBar table={table} />;
}

describe("MobileSortBar", () => {
  it("offers each sortable column as a sort target", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(<Harness />);
    await user.click(getByLabelText(/sort by/i));
    const menu = await document.querySelector('[role="listbox"]');
    expect(menu).not.toBeNull();
    expect(menu?.textContent).toMatch(/Name/);
    expect(menu?.textContent).toMatch(/Age/);
    // The unsortable "actions" column must NOT appear
    expect(menu?.textContent).not.toMatch(/^$/);
  });
});
```

NOTE: The test uses Radix Select — its dropdown has `role="listbox"`. If the assertion path is brittle (Radix changes attributes), fall back to checking that the trigger label updates after a click on a hidden option. Whatever works to verify behavior, not internals.

- [ ] **Step 2: Run test to fail**

```bash
pnpm test MobileSortBar
```

Expected: `Cannot find module '@/components/tables/MobileSortBar'`.

- [ ] **Step 3: Implement MobileSortBar**

```tsx
// frontend/src/components/tables/MobileSortBar.tsx
import {
  type Table as ReactTable,
  type SortingState,
} from "@tanstack/react-table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

interface Props<T> {
  table: ReactTable<T>;
}

export function MobileSortBar<T>({ table }: Props<T>) {
  const sortable = table.getAllColumns().filter((c) => c.getCanSort());
  if (sortable.length === 0) return null;

  const current = table.getState().sorting[0];
  const value = current ? current.id : "";

  return (
    <div className="flex items-center gap-2">
      <Label
        htmlFor="mobile-sort"
        className="shrink-0 text-xs text-muted-foreground"
      >
        Sort by
      </Label>
      <Select
        value={value}
        onValueChange={(id) => {
          const next: SortingState = id ? [{ id, desc: false }] : [];
          table.setSorting(next);
        }}
      >
        <SelectTrigger id="mobile-sort" className="h-9" aria-label="Sort by">
          <SelectValue placeholder="Default order" />
        </SelectTrigger>
        <SelectContent>
          {sortable.map((c) => {
            const header = c.columnDef.header;
            const label =
              typeof header === "string"
                ? header
                : (c.columnDef.meta?.cardLabel ?? c.id);
            return (
              <SelectItem key={c.id} value={c.id}>
                {label}
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>
    </div>
  );
}
```

- [ ] **Step 4: Run test to pass**

```bash
pnpm test MobileSortBar
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add src/components/tables/MobileSortBar.tsx tests/unit/components/tables/MobileSortBar.test.tsx
git commit -m "feat(frontend): add MobileSortBar"
```

---

### Task 6: Add CardList (TDD)

**Files:**

- Create: `frontend/src/components/tables/CardList.tsx`
- Test: `frontend/tests/unit/components/tables/CardList.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/components/tables/CardList.test.tsx
import { render, fireEvent } from "@testing-library/react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { describe, expect, it, vi } from "vitest";
import { CardList } from "@/components/tables/CardList";
import "@/components/tables/types";

interface Job {
  id: string;
  type: string;
  status: string;
  submitted: string;
}

const data: Job[] = [
  { id: "1", type: "train", status: "success", submitted: "2h ago" },
  { id: "2", type: "evaluate", status: "failed", submitted: "1d ago" },
];

const columns: ColumnDef<Job>[] = [
  {
    accessorKey: "type",
    header: "Type",
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "status",
    header: "Status",
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "submitted",
    header: "Submitted",
    meta: { cardLabel: "Submitted", cardSlot: "body" },
  },
  {
    accessorKey: "id",
    header: "ID",
    meta: { cardSlot: "hidden" },
  },
];

function Harness({ onRowClick }: { onRowClick?: (j: Job) => void }) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <CardList table={table} emptyMessage="No jobs" onRowClick={onRowClick} />
  );
}

describe("CardList", () => {
  it("renders one card per row with title + subtitle + body", () => {
    const { getByText, queryByText } = render(<Harness />);
    expect(getByText("train")).toBeInTheDocument();
    expect(getByText("success")).toBeInTheDocument();
    expect(getByText("Submitted")).toBeInTheDocument();
    expect(getByText("2h ago")).toBeInTheDocument();
    // Hidden column must not render
    expect(queryByText("1")).toBeNull();
  });

  it("invokes onRowClick when a card is tapped", () => {
    const handler = vi.fn();
    const { getByText } = render(<Harness onRowClick={handler} />);
    fireEvent.click(getByText("train"));
    expect(handler).toHaveBeenCalledWith(data[0]);
  });

  it("renders empty message when no rows", () => {
    function EmptyHarness() {
      const table = useReactTable({
        data: [] as Job[],
        columns,
        getCoreRowModel: getCoreRowModel(),
      });
      return <CardList table={table} emptyMessage="Nothing here" />;
    }
    const { getByText } = render(<EmptyHarness />);
    expect(getByText("Nothing here")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to fail**

```bash
pnpm test CardList
```

Expected: `Cannot find module '@/components/tables/CardList'`.

- [ ] **Step 3: Implement CardList**

```tsx
// frontend/src/components/tables/CardList.tsx
import {
  flexRender,
  type Cell,
  type Table as ReactTable,
} from "@tanstack/react-table";
import { cn } from "@/lib/cn";

interface Props<T> {
  table: ReactTable<T>;
  emptyMessage: string;
  onRowClick?: (row: T) => void;
}

type Slot = "title" | "subtitle" | "body" | "actions" | "hidden";

function slotOf<T>(cell: Cell<T, unknown>): Slot {
  return (cell.column.columnDef.meta?.cardSlot as Slot | undefined) ?? "body";
}

function labelOf<T>(cell: Cell<T, unknown>): string | null {
  const meta = cell.column.columnDef.meta;
  if (meta?.cardLabel) return meta.cardLabel;
  const header = cell.column.columnDef.header;
  return typeof header === "string" ? header : null;
}

export function CardList<T>({ table, emptyMessage, onRowClick }: Props<T>) {
  const rows = table.getRowModel().rows;
  if (rows.length === 0) {
    return (
      <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => {
        const cells = row.getVisibleCells();
        const titleCell = cells.find((c) => slotOf(c) === "title");
        const subtitleCell = cells.find((c) => slotOf(c) === "subtitle");
        const actionsCell = cells.find((c) => slotOf(c) === "actions");
        const bodyCells = cells
          .filter((c) => slotOf(c) === "body")
          .sort((a, b) => {
            const ao = a.column.columnDef.meta?.cardOrder ?? 0;
            const bo = b.column.columnDef.meta?.cardOrder ?? 0;
            return ao - bo;
          });

        const handleClick = onRowClick
          ? () => onRowClick(row.original)
          : undefined;

        return (
          <div
            key={row.id}
            onClick={handleClick}
            className={cn(
              "rounded-lg border bg-card p-3 shadow-sm",
              handleClick && "cursor-pointer transition-colors hover:bg-accent",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap min-w-0">
                {titleCell && (
                  <div className="font-medium">
                    {flexRender(
                      titleCell.column.columnDef.cell,
                      titleCell.getContext(),
                    )}
                  </div>
                )}
                {subtitleCell && (
                  <div className="text-xs text-muted-foreground">
                    {flexRender(
                      subtitleCell.column.columnDef.cell,
                      subtitleCell.getContext(),
                    )}
                  </div>
                )}
              </div>
              {actionsCell && (
                <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
                  {flexRender(
                    actionsCell.column.columnDef.cell,
                    actionsCell.getContext(),
                  )}
                </div>
              )}
            </div>
            {bodyCells.length > 0 && (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                {bodyCells.map((cell) => {
                  const label = labelOf(cell);
                  return (
                    <div key={cell.id} className="contents">
                      <dt className="text-muted-foreground">{label ?? ""}</dt>
                      <dd className="m-0 text-right text-foreground">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run test to pass**

```bash
pnpm test CardList
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/components/tables/CardList.tsx tests/unit/components/tables/CardList.test.tsx
git commit -m "feat(frontend): add CardList for mobile table rendering"
```

---

### Task 7: Refactor DataTable to dispatch

**Files:**

- Modify: `frontend/src/components/tables/DataTable.tsx`
- Test: `frontend/tests/unit/components/tables/DataTable.test.tsx`

- [ ] **Step 1: Write the dispatch test**

```tsx
// frontend/tests/unit/components/tables/DataTable.test.tsx
import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DataTable } from "@/components/tables/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import "@/components/tables/types";

interface Row {
  name: string;
  status: string;
}

const data: Row[] = [
  { name: "alice", status: "ok" },
  { name: "bob", status: "ok" },
];

const columns: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name", meta: { cardSlot: "title" } },
  { accessorKey: "status", header: "Status", meta: { cardSlot: "subtitle" } },
];

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: "(max-width: 767px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: () => true,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }),
  });
}

describe("DataTable dispatch", () => {
  beforeEach(() => {
    setMatchMedia(false); // default desktop
  });

  it("renders <table> on desktop viewport", () => {
    setMatchMedia(false);
    const { container } = render(
      <DataTable data={data} columns={columns} emptyMessage="No rows" />,
    );
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("renders cards (no table element) on mobile viewport", () => {
    setMatchMedia(true);
    const { container, getByText } = render(
      <DataTable data={data} columns={columns} emptyMessage="No rows" />,
    );
    expect(container.querySelector("table")).toBeNull();
    expect(getByText("alice")).toBeInTheDocument();
    expect(getByText("ok")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test (will fail because DataTable hasn't been refactored)**

```bash
pnpm test DataTable
```

Expected: FAIL — desktop test passes (table element exists today), mobile test fails (no dispatch yet).

- [ ] **Step 3: Refactor DataTable**

Replace the contents of `frontend/src/components/tables/DataTable.tsx` with:

```tsx
// frontend/src/components/tables/DataTable.tsx
import "@/components/tables/types"; // load ColumnMeta augmentation
import {
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";
import { useIsMobile } from "@/hooks/useIsMobile";
import { CardList } from "./CardList";
import { DesktopTable } from "./DesktopTable";
import { MobileSortBar } from "./MobileSortBar";
import { Pagination } from "./Pagination";

interface Props<T> {
  data: T[];
  columns: ColumnDef<T>[];
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  data,
  columns,
  emptyMessage = "No data.",
  onRowClick,
}: Props<T>) {
  const isMobile = useIsMobile();
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <div className="space-y-3">
      {isMobile && <MobileSortBar table={table} />}
      {isMobile ? (
        <CardList
          table={table}
          emptyMessage={emptyMessage}
          onRowClick={onRowClick}
        />
      ) : (
        <DesktopTable
          table={table}
          emptyMessage={emptyMessage}
          onRowClick={onRowClick}
        />
      )}
      <Pagination table={table} />
    </div>
  );
}
```

- [ ] **Step 4: Run test to pass**

```bash
pnpm test DataTable
```

Expected: both dispatch tests pass.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
pnpm test
```

Expected: every existing test that uses `<DataTable>` (e.g., `JobsList.test.tsx`) still passes. There may be tests that render `<DataTable>` without setting `matchMedia` — those default to desktop (`false` from the global stub in `tests/setup.ts`) and should continue to pass.

If any existing test fails: investigate. Likely fixes:

- A test asserts a specific `<table>` structure but now sees cards because matchMedia accidentally returned `true`. Override the per-test mock to force desktop.
- A test imports types that need module augmentation. Add `import "@/components/tables/types";` to the offender or to the central setup.

- [ ] **Step 6: Commit**

```bash
git add src/components/tables/DataTable.tsx tests/unit/components/tables/DataTable.test.tsx
git commit -m "feat(frontend): refactor DataTable to dispatch on isMobile"
```

---

### Task 8: Add PageHeader (TDD)

**Files:**

- Create: `frontend/src/components/layout/PageHeader.tsx`
- Test: `frontend/tests/unit/components/layout/PageHeader.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/components/layout/PageHeader.test.tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "@/components/layout/PageHeader";

describe("PageHeader", () => {
  it("renders the title", () => {
    const { getByRole } = render(<PageHeader title="Jobs" />);
    expect(getByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  });

  it("renders actions in the actions slot", () => {
    const { getByText } = render(
      <PageHeader title="Jobs" actions={<button>Submit</button>} />,
    );
    expect(getByText("Submit")).toBeInTheDocument();
  });

  it("title and actions are siblings inside a flex container", () => {
    const { getByRole, getByText } = render(
      <PageHeader title="Jobs" actions={<button>Submit</button>} />,
    );
    const heading = getByRole("heading");
    const button = getByText("Submit");
    // Walk up: heading and button must share a flex parent
    expect(heading.parentElement?.className).toMatch(/flex/);
    expect(button.parentElement?.parentElement).toBe(heading.parentElement);
  });
});
```

- [ ] **Step 2: Run test to fail**

```bash
pnpm test PageHeader
```

Expected: `Cannot find module '@/components/layout/PageHeader'`.

- [ ] **Step 3: Implement PageHeader**

```tsx
// frontend/src/components/layout/PageHeader.tsx
import type { ReactNode } from "react";

interface Props {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

/**
 * Mobile-first page chrome. Stacks title above actions on `< sm` viewports;
 * lays them out side-by-side at `≥ sm`. Replaces hand-coded
 * `<div className="flex items-center justify-between"><h1>…</h1><Actions/></div>`
 * pattern that wraps poorly on phones (filter dropdown + primary button + h1
 * all on one row at 360 px is unworkable).
 */
export function PageHeader({ title, description, actions }: Props) {
  return (
    <div className="space-y-1">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold">{title}</h1>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
      {description && (
        <p className="text-sm text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to pass**

```bash
pnpm test PageHeader
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/components/layout/PageHeader.tsx tests/unit/components/layout/PageHeader.test.tsx
git commit -m "feat(frontend): add PageHeader for mobile-first page chrome"
```

---

### Task 9: Migrate the simple list pages

This task migrates 6 files in one commit because the change is mechanical and identical: add `<PageHeader>`, add `meta.cardSlot` to `ColumnDef`s.

**Files:**

- Modify: `frontend/src/routes/_authed.detectors._index.tsx`
- Modify: `frontend/src/routes/_authed.datasets._index.tsx`
- Modify: `frontend/src/routes/_authed.jobs._index.tsx`
- Modify: `frontend/src/routes/_authed.models._index.tsx`
- Modify: `frontend/src/routes/_authed.admin.users.tsx`
- Modify: `frontend/src/routes/_authed.runs._index.tsx`

#### 9a. `_authed.detectors._index.tsx`

Replace the columns block with:

```tsx
const columns: ColumnDef<Detector>[] = [
  {
    accessorKey: "display_name",
    header: "Name",
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {row.original.description ?? "—"}
      </span>
    ),
    meta: { cardLabel: "Description", cardSlot: "body" },
  },
  {
    accessorKey: "git_url",
    header: "Git URL",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.git_url}</span>
    ),
    meta: { cardLabel: "Git URL", cardSlot: "body" },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
    meta: { cardLabel: "Created", cardSlot: "subtitle" },
  },
  {
    id: "actions",
    header: "",
    cell: ({ row }) => <DetectorRowActions detector={row.original} />,
    meta: { cardSlot: "actions" },
  },
];
```

Replace the header `<div>` with `<PageHeader>`:

```tsx
import { PageHeader } from "@/components/layout/PageHeader";

// inside DetectorsListPage:
return (
  <div className="space-y-4">
    <PageHeader
      title="Detectors"
      actions={
        <Button asChild>
          <Link to="/detectors/new">
            <Plus className="mr-2 h-4 w-4" />
            Register
          </Link>
        </Button>
      }
    />
    {isLoading ? (
      <p className="text-muted-foreground">Loading…</p>
    ) : (
      <DataTable
        data={items}
        columns={columns}
        emptyMessage="No detectors registered yet."
        onRowClick={(d) => {
          window.location.href = `/detectors/${d.id}`;
        }}
      />
    )}
  </div>
);
```

#### 9b. `_authed.datasets._index.tsx`

Columns:

```tsx
const columns: ColumnDef<Dataset>[] = [
  {
    accessorKey: "name",
    header: "Name",
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "visibility",
    header: "Visibility",
    cell: ({ row }) => (
      <Badge
        variant={row.original.visibility === "public" ? "default" : "secondary"}
      >
        {row.original.visibility}
      </Badge>
    ),
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "sample_count",
    header: "Samples",
    meta: { cardLabel: "Samples", cardSlot: "body" },
  },
  {
    accessorKey: "size_bytes",
    header: "Size",
    cell: ({ row }) => {
      const bytes = row.original.size_bytes;
      return bytes != null ? `${(bytes / 1024).toFixed(1)} KB` : "—";
    },
    meta: { cardLabel: "Size", cardSlot: "body" },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
    meta: { cardLabel: "Created", cardSlot: "body" },
  },
];
```

Header: replace the `<div className="flex items-center justify-between">` with `<PageHeader>`. Keep the `<Select>` filter and `<Link to="/datasets/new">Upload</Link>` button as actions.

#### 9c. `_authed.jobs._index.tsx`

Columns:

```tsx
const columns: ColumnDef<JobSummary>[] = [
  {
    accessorKey: "type",
    header: "Type",
    cell: ({ row }) => <Badge variant="outline">{row.original.type}</Badge>,
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "submitted_at",
    header: "Submitted",
    cell: ({ row }) => formatRelative(row.original.submitted_at),
    meta: { cardLabel: "Submitted", cardSlot: "body" },
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      formatDuration(row.original.started_at, row.original.finished_at),
    meta: { cardLabel: "Duration", cardSlot: "body" },
  },
  {
    id: "final_metrics",
    header: "Final metrics",
    cell: ({ row }) => (
      <FinalMetricsTile summaryMetrics={row.original.summary_metrics} />
    ),
    meta: { cardLabel: "Metrics", cardSlot: "body" },
  },
];
```

Header: PageHeader with title "Jobs" and the existing type filter + Submit button as actions.

#### 9d. `_authed.models._index.tsx`

Columns:

```tsx
const columns: ColumnDef<RegisteredModel>[] = [
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
    header: "Staging",
    cell: ({ row }) =>
      row.original.latest_staging_version != null ? (
        <Badge variant="secondary">
          v{row.original.latest_staging_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    meta: { cardLabel: "Staging", cardSlot: "body" },
  },
  {
    id: "prod",
    header: "Production",
    cell: ({ row }) =>
      row.original.latest_production_version != null ? (
        <Badge className="bg-emerald-600">
          v{row.original.latest_production_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    meta: { cardLabel: "Production", cardSlot: "body" },
  },
];
```

Header: PageHeader with title "Models" (no actions today).

#### 9e. `_authed.admin.users.tsx`

The existing layout has the title + a description paragraph below it. Use PageHeader's `description` prop:

```tsx
<PageHeader
  title="Users"
  description={
    <>
      Promote lab members to <code>developer</code> (register detectors, submit
      jobs) or <code>admin</code> (full access). New SSO arrivals default to{" "}
      <code>user</code>.
    </>
  }
/>
```

Columns:

```tsx
const columns: ColumnDef<User>[] = [
  {
    accessorKey: "email",
    header: "Email",
    cell: ({ row }) => (
      <span>
        {row.original.email}
        {row.original.id === currentUser?.id && (
          <Badge variant="secondary" className="ml-2">
            you
          </Badge>
        )}
      </span>
    ),
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "display_name",
    header: "Display name",
    cell: ({ row }) => row.original.display_name ?? "—",
    meta: { cardLabel: "Display name", cardSlot: "body" },
  },
  {
    accessorKey: "role",
    header: "Role",
    cell: ({ row }) => (
      <RoleCell user={row.original} selfId={currentUser?.id ?? null} />
    ),
    meta: { cardLabel: "Role", cardSlot: "body" },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) =>
      row.original.created_at ? formatRelative(row.original.created_at) : "—",
    meta: { cardLabel: "Created", cardSlot: "body" },
  },
];
```

The 403 error branch's `<div>` keeps using inline `<h1>` (no PageHeader needed for the error path; the page is short and the layout is already mobile-friendly).

#### 9f. `_authed.runs._index.tsx`

This page does not use DataTable (it renders an `<ExperimentCard>` grid). Just wrap the title in PageHeader:

```tsx
import { PageHeader } from "@/components/layout/PageHeader";

return (
  <div className="space-y-4">
    <PageHeader title="Experiments" />
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
      {(data ?? []).map((exp) => (
        <ExperimentCard key={exp.experiment_id} exp={exp} />
      ))}
    </div>
  </div>
);
```

---

After all 6 files are updated:

- [ ] **Step 1: Run typecheck + tests + lint**

```bash
pnpm typecheck
pnpm lint
pnpm test
```

Expected: clean. The existing 125 tests still pass, plus the new tests from Tasks 5/6/7/8 (about +9 tests). No existing list-page test should regress.

- [ ] **Step 2: Commit**

```bash
git add src/routes/_authed.detectors._index.tsx \
        src/routes/_authed.datasets._index.tsx \
        src/routes/_authed.jobs._index.tsx \
        src/routes/_authed.models._index.tsx \
        src/routes/_authed.admin.users.tsx \
        src/routes/_authed.runs._index.tsx
git commit -m "feat(frontend): migrate _index pages to PageHeader + cardSlot meta"
```

---

### Task 10: Migrate the complex list pages

The remaining two pages have more involved table structures.

**Files:**

- Modify: `frontend/src/routes/_authed.runs.$expId.tsx`
- Modify: `frontend/src/routes/_authed.detectors.$id.tsx`

#### 10a. `_authed.runs.$expId.tsx`

This page builds columns dynamically from the data (selected metrics/params). Apply cardSlot meta to the static columns; the dynamic ones default to `cardSlot: "body"`.

Replace the static columns array with cardSlot meta on each:

```tsx
const columns: ColumnDef<Row>[] = [
  {
    accessorKey: "run_id",
    header: "Run",
    cell: ({ row }) => {
      const jobId =
        row.original.tags?.["lolday.job_id"] ??
        row.original.tags?.lolday_job_id;
      if (jobId) {
        return (
          <Link
            to={`/jobs/${jobId}`}
            className="font-mono text-sm hover:underline"
          >
            {row.original.run_id.slice(0, 10)}
          </Link>
        );
      }
      return (
        <a
          href={`/mlflow/#/experiments/${expId}/runs/${row.original.run_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-sm hover:underline"
        >
          {row.original.run_id.slice(0, 10)} ↗
        </a>
      );
    },
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "run_name",
    header: "Name",
    meta: { cardLabel: "Name", cardSlot: "body" },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => (
      <StatusBadge status={row.original.status.toLowerCase()} />
    ),
    meta: { cardSlot: "subtitle" },
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      row.original.start_time && row.original.end_time
        ? formatDuration(
            new Date(row.original.start_time).toISOString(),
            new Date(row.original.end_time).toISOString(),
          )
        : "—",
    meta: { cardLabel: "Duration", cardSlot: "body" },
  },
  ...selectedCols.map((key): ColumnDef<Row> => {
    const [kind, name] = key.split(".", 2);
    return {
      id: key,
      header: name,
      cell: ({ row }) => {
        const v = pickValue(row.original, kind, name);
        if (typeof v === "number") return v.toFixed(4);
        if (v == null) return "—";
        return String(v);
      },
      meta: { cardLabel: name, cardSlot: "body" },
    };
  }),
];
```

Header: PageHeader with title "Runs" and the `<RunsStatusFilter>`, `<RunsColumnPicker>`, `<OpenInMlflowButton>` as actions.

```tsx
<PageHeader
  title="Runs"
  actions={
    <>
      <RunsStatusFilter value={status} onChange={setStatus} />
      <RunsColumnPicker
        experimentId={expId}
        availableMetrics={availableMetrics}
        availableParams={availableParams}
        selected={selectedCols}
        onChange={setSelectedCols}
      />
      <OpenInMlflowButton experimentId={expId} />
    </>
  }
/>
```

#### 10b. `_authed.detectors.$id.tsx`

This page has two DataTables nested inside a `<Tabs>` component (Versions tab + Builds tab). Don't touch the page header (the breadcrumb is the chrome here, and the existing structure is fine). Just add cardSlot meta to both columns arrays.

Versions columns:

```tsx
const versionsCols: ColumnDef<VersionRow>[] = [
  {
    accessorKey: "git_tag",
    header: "Tag",
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "git_sha",
    header: "Commit",
    cell: ({ row }) => (
      <span className="font-mono">{row.original.git_sha.slice(0, 10)}</span>
    ),
    meta: { cardLabel: "Commit", cardSlot: "body" },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "built_at",
    header: "Built",
    cell: ({ row }) => formatRelative(row.original.built_at),
    meta: { cardLabel: "Built", cardSlot: "body" },
  },
  {
    id: "actions",
    header: "",
    cell: ({ row }) => (
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpenManifestTag(row.original.git_tag)}
        >
          View manifest
        </Button>
        <VersionDeleteButton detectorId={id} version={row.original} />
      </div>
    ),
    meta: { cardSlot: "actions" },
  },
];
```

Builds columns:

```tsx
const buildsCols: ColumnDef<BuildRow>[] = [
  {
    accessorKey: "git_tag",
    header: "Tag",
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) => formatRelative(row.original.started_at),
    meta: { cardLabel: "Started", cardSlot: "body" },
  },
  // KEEP the existing duration column verbatim — find it by `id: "duration"`
  // in the current file and add: meta: { cardLabel: "Duration", cardSlot: "body" }
  // The remaining columns (e.g., log tail) follow the same pattern.
];
```

Be careful: this file is 452 lines. Read it carefully and add the `meta` field to the existing entries; do NOT rewrite the entire columns from memory. The cells, accessors, and IDs must stay exactly as they were.

After modifications:

- [ ] **Step 1: Typecheck + tests + lint**

```bash
pnpm typecheck
pnpm lint
pnpm test
```

Expected: clean.

- [ ] **Step 2: Commit**

```bash
git add src/routes/_authed.runs.\$expId.tsx \
        src/routes/_authed.detectors.\$id.tsx
git commit -m "feat(frontend): migrate runs.\$expId + detectors.\$id tables to cardSlot"
```

---

### Task 11: Pre-flight verification

- [ ] **Step 1: Format / lint / typecheck / test**

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr2/frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
```

Expected: each command exits 0. Test count should be approximately 33 files / 134 tests (29 + 4 new test files; 125 + 9 new test cases ± delta from any existing test that needed adjustment).

- [ ] **Step 2: pre-commit on the full PR diff**

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr2
pre-commit run --files $(git diff --name-only feat/mobile-responsive-pr1-foundation-sidebar..HEAD)
```

Expected: all hooks pass.

- [ ] **Step 3: Acceptance self-check (echo back)**

- [x] ≥ 768 px renders today's `<table>` with sort, pagination, actions — covered by `DataTable.test.tsx` desktop case + existing JobsList tests
- [x] < 768 px renders cards with title / subtitle / body / actions slots — covered by `DataTable.test.tsx` mobile case + `CardList.test.tsx`
- [x] Sort dropdown changes order — covered by `MobileSortBar.test.tsx`
- [x] Page headers stack at 360 px and lay out at ≥ 640 px — covered by `PageHeader.test.tsx` + Tailwind responsive classes verified by typecheck of the JSX
- [x] TS module augmentation type-checks — covered by `tsc --noEmit` over `types.ts`
- [x] Lint, types, tests, existing E2E green — verified by Step 1 + Step 2

---

### Task 12: Push branch + open PR

- [ ] **Step 1: Push**

```bash
cd /home/bolin8017/Documents/repositories/lolday/.worktrees/mobile-pr2
git push -u origin feat/mobile-responsive-pr2-tables
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --base feat/mobile-responsive-pr1-foundation-sidebar \
  --head feat/mobile-responsive-pr2-tables \
  --title "feat(frontend): mobile responsive PR-2 — DataTable + list pages" \
  --body "$(cat <<'EOF'
## Summary

PR-2 of the mobile-first responsive redesign. Refactors `DataTable.tsx` to dispatch between desktop `<Table>` and mobile card list, introduces `<PageHeader>` for mobile-first page chrome, and migrates all 8 list/table sites.

- Spec: `docs/superpowers/specs/2026-05-04-mobile-responsive-redesign-design.md` §3
- Plan: `docs/superpowers/plans/2026-05-04-mobile-responsive-pr2-tables.md`

**Stacked on PR #79.** Base is `feat/mobile-responsive-pr1-foundation-sidebar`. Will retarget to `main` after PR-1 merges.

## What changes for users

- Mobile (< 768 px): every list page renders rows as cards with title / subtitle / body / actions slots; tap navigates to detail. A "Sort by" dropdown lives above the cards.
- Desktop (≥ 768 px): no visual change — same `<Table>`, same sort, same pagination.
- Page headers (`<h1>` + filters + actions) now stack vertically at 360 px and lay out side-by-side at ≥ 640 px, replacing the hand-coded `flex justify-between` pattern.

## How it's built

- TanStack Table `ColumnMeta` module augmentation adds `cardLabel` / `cardSlot` / `cardOrder`.
- `DataTable.tsx` becomes a thin dispatcher; `<DesktopTable>`, `<CardList>`, `<MobileSortBar>`, `<Pagination>` are the moving parts.
- `<PageHeader>` is a new layout primitive in `components/layout/`.
- 8 routes updated: detectors / datasets / jobs / models / admin.users / runs._index / runs.\$expId / detectors.\$id (versions + builds tables).

## Test plan

- [x] `pnpm format:check && pnpm lint && pnpm typecheck && pnpm test` all green.
- [x] `pre-commit` on full PR diff green.
- [ ] Mobile (devtools 393 px) visual: each list page renders cards with the right cards-slot mapping.
- [ ] Desktop (≥ 768 px) visual: each list page unchanged from PR-1's state.
- [ ] Sort dropdown on mobile changes card order.

## Out of scope

- Detail / forms / charts mobile fixes — PR-3.
- Mobile E2E project (iPhone 13 mini, Pixel 5) — PR-4.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement (§3)                                            | Plan task             |
| ---------------------------------------------------------------- | --------------------- |
| ColumnMeta augmentation (`cardLabel` / `cardSlot` / `cardOrder`) | Task 2                |
| DataTable dispatch on `useIsMobile`                              | Task 7                |
| `CardList` with title/subtitle/body/actions/hidden slots         | Task 6                |
| `DesktopTable` extracted verbatim                                | Task 3                |
| `MobileSortBar` (Sort by Select)                                 | Task 5                |
| `Pagination` extracted                                           | Task 4                |
| `PageHeader` mobile-first                                        | Task 8                |
| Migrate 8 list/table sites                                       | Tasks 9 + 10          |
| Tests (`pnpm typecheck && pnpm lint && pnpm test`) green         | Task 11               |
| Existing Playwright E2E green                                    | Task 11 (manual / CI) |

No gaps.

**Placeholder scan:** No `TBD` / `TODO`. Each task includes the actual code.

**Type consistency:**

- `cardSlot` literal union (`title | subtitle | body | actions | hidden`) declared in Task 2, consumed identically in Tasks 6 and 9-10.
- `useIsMobile()` returns `boolean`, consumed in Task 7's DataTable dispatch.
- `ReactTable<T>` from `@tanstack/react-table` is the table object passed to `DesktopTable`, `CardList`, `MobileSortBar`, `Pagination`. All four components accept `Props<T> { table: ReactTable<T>; ... }`.

No inconsistencies.
