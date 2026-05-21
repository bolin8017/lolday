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

  it("clicking the Family column header sorts rows alphabetically (L60 + L151 sort-by-name)", async () => {
    // Data ordered count-desc by default: wannacry (50) > dridex (30) > emotet (10).
    // After clicking the Family header, alphabetic order is: dridex, emotet, wannacry.
    const data = { wannacry: 50, dridex: 30, emotet: 10 };
    render(<FamilyDistribution data={data} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Show all 3 families/ }),
    );
    const table = await screen.findByRole("table");
    // Sanity: default order is count-desc (wannacry first).
    const beforeRows = within(table).getAllByRole("row").slice(1); // drop header
    expect(beforeRows[0]).toHaveTextContent("wannacry");
    // Click the column header button (the inner <button> inside <th>).
    await userEvent.click(
      within(table).getByRole("button", { name: "Family" }),
    );
    const afterRows = within(table).getAllByRole("row").slice(1);
    expect(afterRows[0]).toHaveTextContent("dridex");
    expect(afterRows[1]).toHaveTextContent("emotet");
    expect(afterRows[2]).toHaveTextContent("wannacry");
    // aria-sort flips on the Family <th> per the route handler.
    const familyHeader = within(table)
      .getByRole("button", { name: "Family" })
      .closest("th");
    expect(familyHeader).toHaveAttribute("aria-sort", "ascending");
  });

  it("clicking the Count column header restores the count-desc default sort (L164 sort-by-count)", async () => {
    const data = { wannacry: 50, dridex: 30, emotet: 10 };
    render(<FamilyDistribution data={data} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Show all 3 families/ }),
    );
    const table = await screen.findByRole("table");
    // Flip to alphabetic first so the Count click is observable as a re-sort.
    await userEvent.click(
      within(table).getByRole("button", { name: "Family" }),
    );
    expect(within(table).getAllByRole("row").slice(1)[0]).toHaveTextContent(
      "dridex",
    );
    // Now click Count — back to count-desc (wannacry at top).
    await userEvent.click(within(table).getByRole("button", { name: "Count" }));
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("wannacry");
    const countHeader = within(table)
      .getByRole("button", { name: "Count" })
      .closest("th");
    expect(countHeader).toHaveAttribute("aria-sort", "descending");
  });
});
