import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { describe, expect, it } from "vitest";
import { Pagination } from "@/components/tables/Pagination";

interface Row {
  v: number;
}

const columns: ColumnDef<Row>[] = [{ accessorKey: "v", header: "V" }];

function buildHarness({ rows, pageSize }: { rows: number; pageSize: number }) {
  const data: Row[] = Array.from({ length: rows }, (_, i) => ({ v: i }));
  return function Harness() {
    const table = useReactTable({
      data,
      columns,
      getCoreRowModel: getCoreRowModel(),
      getPaginationRowModel: getPaginationRowModel(),
      initialState: { pagination: { pageIndex: 0, pageSize } },
    });
    return <Pagination table={table} />;
  };
}

describe("Pagination", () => {
  it("disables Prev on the first page", () => {
    const Harness = buildHarness({ rows: 30, pageSize: 10 });
    render(<Harness />);
    const prev = screen.getByRole("button", { name: /prev|上一頁/i });
    expect(prev).toBeDisabled();
  });

  it("Next click advances to the next page", async () => {
    const user = userEvent.setup();
    const Harness = buildHarness({ rows: 30, pageSize: 10 });
    render(<Harness />);
    expect(
      screen.getByText(/page 1 of 3|第 1 頁 \/ 共 3 頁/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /next|下一頁/i }));
    expect(
      screen.getByText(/page 2 of 3|第 2 頁 \/ 共 3 頁/i),
    ).toBeInTheDocument();
  });

  it("disables Next on the last page", async () => {
    const user = userEvent.setup();
    const Harness = buildHarness({ rows: 25, pageSize: 10 });
    render(<Harness />);
    const next = screen.getByRole("button", { name: /next|下一頁/i });
    await user.click(next);
    await user.click(next);
    expect(next).toBeDisabled();
  });

  it("falls back to 'Page 1 of 1' when data is empty", () => {
    const Harness = buildHarness({ rows: 0, pageSize: 10 });
    render(<Harness />);
    expect(
      screen.getByText(/page 1 of 1|第 1 頁 \/ 共 1 頁/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prev|上一頁/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next|下一頁/i })).toBeDisabled();
  });
});
