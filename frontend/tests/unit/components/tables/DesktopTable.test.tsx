import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Table as ReactTable,
} from "@tanstack/react-table";
import { describe, expect, it, vi } from "vitest";
import { DesktopTable } from "@/components/tables/DesktopTable";

interface Row {
  name: string;
  status: string;
}

const data: Row[] = [
  { name: "alice", status: "ok" },
  { name: "bob", status: "ok" },
];

const columns: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name" },
  { accessorKey: "status", header: "Status" },
];

function Harness({
  rows = data,
  onRowClick,
  tableRef,
}: {
  rows?: Row[];
  onRowClick?: (row: Row) => void;
  tableRef?: { current: ReactTable<Row> | null };
}) {
  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  if (tableRef) tableRef.current = table;
  return (
    <DesktopTable
      table={table}
      emptyMessage="No rows"
      onRowClick={onRowClick}
    />
  );
}

describe("DesktopTable", () => {
  it("renders a row per data item", () => {
    render(<Harness />);
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
  });

  it("clicking a header toggles sorting", async () => {
    const user = userEvent.setup();
    const tableRef: { current: ReactTable<Row> | null } = { current: null };
    render(<Harness tableRef={tableRef} />);
    const nameHeader = screen.getByRole("button", { name: /name/i });
    await user.click(nameHeader);
    expect(tableRef.current?.getState().sorting).toEqual([
      { id: "name", desc: false },
    ]);
  });

  it("renders an empty-state row spanning all visible leaf columns", () => {
    render(<Harness rows={[]} />);
    const emptyCell = screen.getByText("No rows");
    expect(emptyCell).toBeInTheDocument();
    // colSpan should match the visible leaf column count (2: name + status).
    expect(emptyCell).toHaveAttribute("colspan", "2");
  });

  it("invokes onRowClick with the clicked row", async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness onRowClick={handler} />);
    await user.click(screen.getByText("alice"));
    expect(handler).toHaveBeenCalledWith(data[0]);
  });

  it("does NOT add cursor-pointer when no onRowClick is provided", () => {
    const { container } = render(<Harness />);
    const rows = container.querySelectorAll("tbody tr");
    rows.forEach((tr) => {
      expect(tr.className).not.toMatch(/cursor-pointer/);
    });
  });
});
