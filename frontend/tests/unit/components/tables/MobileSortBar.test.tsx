import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Table as ReactTable,
} from "@tanstack/react-table";
import { describe, expect, it } from "vitest";
import { MobileSortBar } from "@/components/tables/MobileSortBar";
import "@/components/tables/types";

interface Row {
  name: string;
  age: number;
  internalId: string;
}

const data: Row[] = [
  { name: "alice", age: 30, internalId: "u-1" },
  { name: "bob", age: 25, internalId: "u-2" },
];

const columns: ColumnDef<Row>[] = [
  { accessorKey: "name", header: "Name" },
  { accessorKey: "age", header: "Age" },
  // hidden cardSlot — sortable but should NOT show in mobile sort dropdown
  {
    accessorKey: "internalId",
    header: "Internal ID",
    meta: { cardSlot: "hidden" },
  },
  { id: "actions", header: "", enableSorting: false },
];

function Harness({
  tableRef,
}: {
  tableRef?: { current: ReactTable<Row> | null };
}) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  if (tableRef) tableRef.current = table;
  return <MobileSortBar table={table} />;
}

describe("MobileSortBar", () => {
  it("offers each visible sortable column as a sort target", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(<Harness />);
    await user.click(getByLabelText(/sort by|排序依據/i));
    const options = await screen.findAllByRole("option");
    const labels = options.map((el) => el.textContent ?? "").filter(Boolean);
    expect(labels).toContain("Name");
    expect(labels).toContain("Age");
    expect(labels).not.toContain("Internal ID");
    expect(labels.length).toBe(2);
  });

  it("calls table.setSorting with the chosen column", async () => {
    const user = userEvent.setup();
    const tableRef: { current: ReactTable<Row> | null } = { current: null };
    render(<Harness tableRef={tableRef} />);
    await user.click(screen.getByLabelText(/sort by|排序依據/i));
    const ageOption = await screen.findByRole("option", { name: "Age" });
    await user.click(ageOption);
    expect(tableRef.current?.getState().sorting).toEqual([
      { id: "age", desc: false },
    ]);
  });

  it("renders nothing when no column is sortable", () => {
    const allUnsortable: ColumnDef<Row>[] = [
      { accessorKey: "name", header: "Name", enableSorting: false },
      { accessorKey: "age", header: "Age", enableSorting: false },
    ];
    function EmptyHarness() {
      const table = useReactTable({
        data,
        columns: allUnsortable,
        getCoreRowModel: getCoreRowModel(),
      });
      return <MobileSortBar table={table} />;
    }
    const { container } = render(<EmptyHarness />);
    expect(container).toBeEmptyDOMElement();
  });
});
