import { render, fireEvent } from "@testing-library/react";
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
    // Two rows both render the "Submitted" label — use getAllByText
    expect(getAllByText("Submitted").length).toBeGreaterThanOrEqual(1);
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

  it("clicking the actions cell does not invoke onRowClick", () => {
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
    const { getAllByText } = render(<HarnessWithActions />);
    fireEvent.click(getAllByText("Delete")[0]);
    expect(actionHandler).toHaveBeenCalled();
    expect(rowHandler).not.toHaveBeenCalled();
  });
});
