import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UserParamsTable } from "@/components/jobs/UserParamsTable";

/**
 * ``UserParamsTable`` is the leaf used inside ``ResolvedConfigCard``
 * (which is itself the leaf used by all three job-summary tiles). It
 * was stubbed when ``ResolvedConfigCard`` was tested in #367; this PR
 * pins the leaf's own contract.
 *
 * Behaviours covered:
 *
 * - Empty user-params map → friendly empty-state message, no table.
 * - Keys are alphabetised.
 * - Value cells JSON-stringify primitives, arrays, and objects.
 * - When defaults are supplied: third column "Default" appears,
 *   user-value == default is annotated "(default)", user-value !=
 *   default has no annotation.
 * - When defaults is null: only two columns; no annotations.
 * - Default cell prints "—" when the key is absent from defaults.
 */

describe("UserParamsTable", () => {
  it("shows the empty-state message when userParams is empty", () => {
    render(<UserParamsTable userParams={{}} defaults={null} />);
    expect(
      screen.getByText(
        /No hyperparameters submitted \(used detector defaults\)\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("alphabetises parameter rows", () => {
    render(
      <UserParamsTable
        userParams={{ zeta: 1, alpha: 2, gamma: 3 }}
        defaults={null}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1); // skip header
    const names = rows.map(
      (r) => within(r).getAllByRole("cell")[0].textContent,
    );
    expect(names).toEqual(["alpha", "gamma", "zeta"]);
  });

  it("omits the Default column entirely when defaults is null", () => {
    render(<UserParamsTable userParams={{ lr: 0.001 }} defaults={null} />);
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Parameter", "Your value"]);
    expect(screen.queryByText("(default)")).not.toBeInTheDocument();
  });

  it("includes the Default column when defaults is supplied", () => {
    render(
      <UserParamsTable userParams={{ lr: 0.001 }} defaults={{ lr: 0.01 }} />,
    );
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Parameter", "Your value", "Default"]);
  });

  it("annotates rows where the user value matches the default", () => {
    render(
      <UserParamsTable
        userParams={{ lr: 0.001, epochs: 10 }}
        defaults={{ lr: 0.001, epochs: 20 }}
      />,
    );
    // lr matches default → has "(default)"
    const lrRow = screen.getByText("lr").closest("tr")!;
    expect(within(lrRow).getByText("(default)")).toBeInTheDocument();
    // epochs differs → no "(default)"
    const epochsRow = screen.getByText("epochs").closest("tr")!;
    expect(within(epochsRow).queryByText("(default)")).not.toBeInTheDocument();
  });

  it("renders an em-dash in the Default cell when the key is absent from defaults", () => {
    render(
      <UserParamsTable
        userParams={{ lr: 0.001, custom: 42 }}
        defaults={{ lr: 0.01 }}
      />,
    );
    const customRow = screen.getByText("custom").closest("tr")!;
    const cells = within(customRow).getAllByRole("cell");
    // [parameter, your value, default]
    expect(cells[2].textContent).toBe("—");
  });

  it("JSON-stringifies non-primitive values in the Your value cell", () => {
    render(
      <UserParamsTable userParams={{ layers: [16, 32, 64] }} defaults={null} />,
    );
    expect(screen.getByText("[16,32,64]")).toBeInTheDocument();
  });
});
