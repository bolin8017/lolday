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
    const { container, getByText, getAllByText } = render(
      <DataTable data={data} columns={columns} emptyMessage="No rows" />,
    );
    expect(container.querySelector("table")).toBeNull();
    expect(getByText("alice")).toBeInTheDocument();
    // both rows share the same status value — getAllByText covers multiple cards
    expect(getAllByText("ok").length).toBeGreaterThanOrEqual(1);
  });
});
