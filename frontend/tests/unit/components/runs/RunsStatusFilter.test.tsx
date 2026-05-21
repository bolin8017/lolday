import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  isRunsStatus,
  RUNS_STATUSES,
  RunsStatusFilter,
} from "@/components/runs/RunsStatusFilter";

describe("isRunsStatus", () => {
  it("accepts every value in the RUNS_STATUSES tuple", () => {
    for (const s of RUNS_STATUSES) {
      expect(isRunsStatus(s)).toBe(true);
    }
  });

  it("rejects an empty string", () => {
    expect(isRunsStatus("")).toBe(false);
  });

  it("rejects a non-member string", () => {
    expect(isRunsStatus("pending")).toBe(false);
    expect(isRunsStatus("succeeded")).toBe(false); // backend uses succeeded; MLflow uses FINISHED
  });

  it("rejects non-string inputs (URL search-param parsing edge case)", () => {
    // useSearchParams returns strings, but the call site forwards `?status=`
    // values through this guard precisely to fail closed on hand-crafted
    // URLs with non-string-y query payloads.
    expect(isRunsStatus(undefined)).toBe(false);
    expect(isRunsStatus(null)).toBe(false);
    expect(isRunsStatus(42)).toBe(false);
    expect(isRunsStatus(["FINISHED"])).toBe(false);
  });

  it("RUNS_STATUSES is the exact tuple — pinning the 5 MLflow-mapped values", () => {
    // A regression that quietly drops one status (e.g. SCHEDULED) would
    // produce a UI dropdown missing an item without crashing — pin the
    // exact tuple shape here so future drift surfaces in this assertion.
    expect([...RUNS_STATUSES]).toEqual([
      "all",
      "FINISHED",
      "RUNNING",
      "FAILED",
      "SCHEDULED",
    ]);
  });
});

describe("RunsStatusFilter", () => {
  it("renders the trigger with aria-label='Filter by run status' (axe-pass)", () => {
    render(<RunsStatusFilter value="all" onChange={() => {}} />);
    expect(
      screen.getByRole("combobox", { name: /filter by run status/i }),
    ).toBeInTheDocument();
  });

  it("renders the 'All statuses' text when value='all'", () => {
    render(<RunsStatusFilter value="all" onChange={() => {}} />);
    expect(screen.getByText(/all statuses/i)).toBeInTheDocument();
  });

  it("renders the raw MLflow status when a non-all value is selected", () => {
    render(<RunsStatusFilter value="FINISHED" onChange={() => {}} />);
    // The trigger label shows the value verbatim (uppercase MLflow status).
    expect(screen.getByText("FINISHED")).toBeInTheDocument();
  });

  it("invokes onChange with the selected status (covers L34 onValueChange guard)", async () => {
    // Pins the L34 `if (isRunsStatus(v)) onChange(v)` happy path. Without
    // this assertion an `onChange` regression (e.g. typo dropping the call)
    // would still let the trigger render correctly while silently no-op-ing
    // user selections.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RunsStatusFilter value="all" onChange={onChange} />);

    await user.click(
      screen.getByRole("combobox", { name: /filter by run status/i }),
    );
    // SelectContent renders in a portal; query by role inside the document.
    const finished = await screen.findByRole("option", { name: "FINISHED" });
    await user.click(finished);

    expect(onChange).toHaveBeenCalledWith("FINISHED");
  });
});
