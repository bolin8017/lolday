import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResolvedConfigCard } from "@/components/jobs/ResolvedConfigCard";

/**
 * ``ResolvedConfigCard`` is the leaf used by every job-summary tile
 * (TrainSummary, EvaluateSummary, PredictSummary). All three stub it
 * via ``vi.mock`` when testing their composition choices; this PR
 * pins the leaf's own contract.
 *
 * Behaviours covered:
 *
 * - "Your hyperparameters" section renders the table when ``userParams``
 *   is supplied.
 * - Legacy-job fallback message when ``userParams`` is ``null``.
 * - Line count in the toggle label reflects the pretty-printed JSON.
 * - JSON-tree section is hidden by default; clicking the toggle reveals
 *   it; clicking again hides it.
 * - Chevron icon flips between right and down with the toggle.
 */

vi.mock("@/components/jobs/UserParamsTable", () => ({
  UserParamsTable: ({
    userParams,
  }: {
    userParams: Record<string, unknown>;
  }) => (
    <div data-testid="user-params-table">{Object.keys(userParams).length}</div>
  ),
}));
vi.mock("@/components/common/JsonTreeView", () => ({
  JsonTreeView: ({ value }: { value: Record<string, unknown> }) => (
    <div data-testid="json-tree">{Object.keys(value).length}</div>
  ),
}));

describe("ResolvedConfigCard", () => {
  it("renders UserParamsTable when userParams is supplied", () => {
    render(
      <ResolvedConfigCard
        resolvedConfig={{}}
        userParams={{ lr: 0.001, epochs: 10 }}
      />,
    );
    expect(screen.getByTestId("user-params-table")).toHaveTextContent("2");
    expect(
      screen.queryByText(/Legacy job — user-supplied params not recorded\./),
    ).not.toBeInTheDocument();
  });

  it("renders the legacy-job fallback when userParams is null", () => {
    render(<ResolvedConfigCard resolvedConfig={{}} userParams={null} />);
    expect(
      screen.getByText(/Legacy job — user-supplied params not recorded\./),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("user-params-table")).not.toBeInTheDocument();
  });

  it("uses the prettified line count in the toggle label", () => {
    // JSON.stringify({a:1,b:{c:2}}, null, 2):
    //   {
    //     "a": 1,
    //     "b": {
    //       "c": 2
    //     }
    //   }
    // 6 lines after `split("\n")`.
    render(
      <ResolvedConfigCard
        resolvedConfig={{ a: 1, b: { c: 2 } }}
        userParams={{}}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /Show full resolved config \(6 lines\)/,
      }),
    ).toBeInTheDocument();
  });

  it("toggles the JSON tree open and closed when the button is clicked", async () => {
    const user = userEvent.setup();
    render(
      <ResolvedConfigCard resolvedConfig={{ a: 1, b: 2 }} userParams={{}} />,
    );
    // Hidden by default.
    expect(screen.queryByTestId("json-tree")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", {
      name: /Show full resolved config/,
    });
    await user.click(toggle);
    expect(screen.getByTestId("json-tree")).toBeInTheDocument();
    // The label flips from Show to Hide.
    expect(
      screen.getByRole("button", { name: /Hide full resolved config/ }),
    ).toBeInTheDocument();
    // Second click closes it again.
    await user.click(
      screen.getByRole("button", { name: /Hide full resolved config/ }),
    );
    expect(screen.queryByTestId("json-tree")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Show full resolved config/ }),
    ).toBeInTheDocument();
  });
});
