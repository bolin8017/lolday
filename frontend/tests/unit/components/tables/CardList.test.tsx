import { render, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
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
    const { getByText, getAllByText, queryByText } = render(<Harness />);
    expect(getByText("train")).toBeInTheDocument();
    expect(getByText("success")).toBeInTheDocument();
    expect(getAllByText("Submitted").length).toBeGreaterThanOrEqual(1);
    expect(getByText("2h ago")).toBeInTheDocument();
    // Hidden column must not render anywhere on the card
    expect(queryByText("1")).toBeNull();
  });

  it("invokes onRowClick when a non-interactive area is tapped", () => {
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

  it("clicking an action button does NOT invoke onRowClick (closest() guard)", async () => {
    const user = userEvent.setup();
    const rowHandler = vi.fn();
    const actionHandler = vi.fn();
    const columnsWithActions: ColumnDef<Job>[] = [
      {
        accessorKey: "type",
        header: "Type",
        meta: { cardSlot: "title" },
      },
      {
        id: "actions",
        header: "",
        cell: () => <button onClick={actionHandler}>Delete</button>,
        meta: { cardSlot: "actions" },
      },
    ];
    function HarnessWithActions() {
      const table = useReactTable({
        data,
        columns: columnsWithActions,
        getCoreRowModel: getCoreRowModel(),
      });
      return (
        <CardList
          table={table}
          emptyMessage="No jobs"
          onRowClick={rowHandler}
        />
      );
    }
    const { getAllByRole } = render(<HarnessWithActions />);
    await user.click(getAllByRole("button", { name: "Delete" })[0]!);
    expect(actionHandler).toHaveBeenCalled();
    expect(rowHandler).not.toHaveBeenCalled();
  });

  it("actions slot stops bubbling for non-interactive children (defense-in-depth)", () => {
    // The smart click handler ignores clicks on `button|a|...` via closest().
    // But a tooltip wrapper (plain <span>) inside the actions slot is NOT
    // interactive — only the actions slot's stopPropagation prevents the
    // card-level handler from firing in that case.
    const rowHandler = vi.fn();
    const columnsWithSpanActions: ColumnDef<Job>[] = [
      {
        accessorKey: "type",
        header: "Type",
        meta: { cardSlot: "title" },
      },
      {
        id: "actions",
        header: "",
        cell: () => <span data-testid="action-tooltip">tooltip</span>,
        meta: { cardSlot: "actions" },
      },
    ];
    function HarnessWithSpanActions() {
      const table = useReactTable({
        data,
        columns: columnsWithSpanActions,
        getCoreRowModel: getCoreRowModel(),
      });
      return (
        <CardList
          table={table}
          emptyMessage="No jobs"
          onRowClick={rowHandler}
        />
      );
    }
    const { getAllByTestId } = render(<HarnessWithSpanActions />);
    fireEvent.click(getAllByTestId("action-tooltip")[0]!);
    expect(rowHandler).not.toHaveBeenCalled();
  });

  it("body slot interactive content does NOT trigger onRowClick", async () => {
    // RoleCell-style scenario: a Select / button rendered inside cardSlot=body.
    // The smart click handler must ignore the click via closest().
    const user = userEvent.setup();
    const rowHandler = vi.fn();
    const inlineHandler = vi.fn();
    const columnsWithBodyButton: ColumnDef<Job>[] = [
      {
        accessorKey: "type",
        header: "Type",
        meta: { cardSlot: "title" },
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: () => <button onClick={inlineHandler}>Promote</button>,
        meta: { cardLabel: "Status", cardSlot: "body" },
      },
    ];
    function HarnessWithBodyButton() {
      const table = useReactTable({
        data,
        columns: columnsWithBodyButton,
        getCoreRowModel: getCoreRowModel(),
      });
      return (
        <CardList
          table={table}
          emptyMessage="No jobs"
          onRowClick={rowHandler}
        />
      );
    }
    const { getAllByRole } = render(<HarnessWithBodyButton />);
    await user.click(getAllByRole("button", { name: "Promote" })[0]!);
    expect(inlineHandler).toHaveBeenCalled();
    expect(rowHandler).not.toHaveBeenCalled();
  });

  it("respects cardOrder ascending for body cells", () => {
    const orderedColumns: ColumnDef<Job>[] = [
      {
        accessorKey: "type",
        header: "Type",
        meta: { cardSlot: "title" },
      },
      {
        accessorKey: "submitted",
        header: "Submitted",
        meta: { cardLabel: "Submitted", cardSlot: "body", cardOrder: 2 },
      },
      {
        accessorKey: "status",
        header: "Status",
        meta: { cardLabel: "Status", cardSlot: "body", cardOrder: 1 },
      },
    ];
    function OrderedHarness() {
      const table = useReactTable({
        data,
        columns: orderedColumns,
        getCoreRowModel: getCoreRowModel(),
      });
      return <CardList table={table} emptyMessage="empty" />;
    }
    const { getAllByText } = render(<OrderedHarness />);
    // The first Status label should appear before the first Submitted label
    // in the DOM, because cardOrder:1 < cardOrder:2.
    const statusEl = getAllByText("Status")[0]!;
    const submittedEl = getAllByText("Submitted")[0]!;
    expect(
      statusEl.compareDocumentPosition(submittedEl) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
