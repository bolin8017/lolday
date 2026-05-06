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
