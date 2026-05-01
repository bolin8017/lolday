import { render, screen, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { UserParamsTable } from "@/components/jobs/UserParamsTable";

/**
 * Returns the user-value cell (column index 1) for the row whose
 * Parameter cell (column index 0) text-matches `paramName`.
 *
 * Using row + column index avoids ambiguity when the user value and the
 * default value cells render the same string (e.g. both "null").
 */
function userValueCellFor(paramName: string): HTMLTableCellElement {
  const rows = screen.getAllByRole("row");
  for (const row of rows) {
    const cells = within(row).queryAllByRole("cell");
    if (cells.length > 0 && cells[0].textContent === paramName) {
      return cells[1] as HTMLTableCellElement;
    }
  }
  throw new Error(`No row found for parameter "${paramName}"`);
}

describe("UserParamsTable", () => {
  it("renders empty-state message when userParams is empty", () => {
    render(<UserParamsTable userParams={{}} defaults={null} />);
    expect(
      screen.getByText(/no hyperparameters submitted/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("omits the Default column when defaults is null and styles every value as an override", () => {
    render(
      <UserParamsTable userParams={{ n_estimators: 200 }} defaults={null} />,
    );
    expect(
      screen.getByRole("columnheader", { name: "Parameter" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Your value" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Default" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/\(default\)/i)).not.toBeInTheDocument();
    const cell = screen.getByText("200");
    expect(cell).toHaveClass("font-medium");
  });

  it("renders the Default column when defaults is provided", () => {
    render(
      <UserParamsTable
        userParams={{ n_estimators: 200 }}
        defaults={{ n_estimators: 100 }}
      />,
    );
    expect(
      screen.getByRole("columnheader", { name: "Default" }),
    ).toBeInTheDocument();
  });

  it("marks default-matching rows with a (default) suffix and muted text", () => {
    render(
      <UserParamsTable
        userParams={{ n_estimators: 100, max_depth: null }}
        defaults={{ n_estimators: 100, max_depth: null }}
      />,
    );
    expect(screen.getAllByText(/\(default\)/i)).toHaveLength(2);
    for (const param of ["n_estimators", "max_depth"]) {
      const cell = userValueCellFor(param);
      expect(cell).toHaveClass("text-muted-foreground");
      expect(cell).not.toHaveClass("font-medium");
    }
  });

  it("marks override rows with font-medium and no (default) suffix", () => {
    render(
      <UserParamsTable
        userParams={{ n_estimators: 200, max_depth: null }}
        defaults={{ n_estimators: 100, max_depth: null }}
      />,
    );
    // Only one row matches the default (max_depth: null === null).
    expect(screen.getAllByText(/\(default\)/i)).toHaveLength(1);

    const overrideCell = userValueCellFor("n_estimators");
    expect(overrideCell).toHaveClass("font-medium");
    expect(overrideCell).not.toHaveClass("text-muted-foreground");

    const matchingCell = userValueCellFor("max_depth");
    expect(matchingCell).toHaveClass("text-muted-foreground");
    expect(matchingCell).not.toHaveClass("font-medium");
  });

  it("treats matching null defaults as default (sklearn max_depth=null is meaningful)", () => {
    render(
      <UserParamsTable
        userParams={{ max_depth: null }}
        defaults={{ max_depth: null }}
      />,
    );
    expect(screen.getByText(/\(default\)/i)).toBeInTheDocument();
    const cell = userValueCellFor("max_depth");
    expect(cell).toHaveClass("text-muted-foreground");
  });

  it("uses deep equality for nested dict defaults", () => {
    const { rerender } = render(
      <UserParamsTable
        userParams={{ opts: { a: 1, b: 2 } }}
        defaults={{ opts: { a: 1, b: 2 } }}
      />,
    );
    expect(screen.getByText(/\(default\)/i)).toBeInTheDocument();

    rerender(
      <UserParamsTable
        userParams={{ opts: { a: 1, b: 99 } }}
        defaults={{ opts: { a: 1, b: 2 } }}
      />,
    );
    expect(screen.queryByText(/\(default\)/i)).not.toBeInTheDocument();
    const overrideCell = screen.getByText('{"a":1,"b":99}').closest("td");
    expect(overrideCell).toHaveClass("font-medium");
  });

  it("renders the literal default value in the Default column", () => {
    render(
      <UserParamsTable
        userParams={{ n_estimators: 200 }}
        defaults={{ n_estimators: 100 }}
      />,
    );
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it('renders "—" in the Default column when no matching default exists', () => {
    render(
      <UserParamsTable
        userParams={{ custom_field: "hi" }}
        defaults={{ n_estimators: 100 }}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
