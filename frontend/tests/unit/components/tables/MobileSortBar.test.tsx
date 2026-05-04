import { render, screen } from "@testing-library/react";
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
    const options = await screen.findAllByRole("option");
    const labels = options.map((el) => el.textContent ?? "").filter(Boolean);
    expect(labels).toContain("Name");
    expect(labels).toContain("Age");
    expect(labels.length).toBe(2);
  });
});
