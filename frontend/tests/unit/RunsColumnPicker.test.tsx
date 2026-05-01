import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  RunsColumnPicker,
  loadColumnsFromStorage,
} from "@/components/runs/RunsColumnPicker";

describe("RunsColumnPicker", () => {
  beforeEach(() => localStorage.clear());

  it("calls onChange when a metric is toggled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <RunsColumnPicker
        experimentId="1"
        availableMetrics={["accuracy", "f1"]}
        availableParams={["lr"]}
        selected={["metrics.accuracy"]}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /columns/i }));
    await user.click(screen.getByText("f1"));
    expect(onChange).toHaveBeenCalledWith(["metrics.accuracy", "metrics.f1"]);
  });

  it("persists to localStorage on change", () => {
    render(
      <RunsColumnPicker
        experimentId="1"
        availableMetrics={["accuracy"]}
        availableParams={[]}
        selected={["metrics.accuracy"]}
        onChange={() => {}}
      />,
    );
    expect(localStorage.getItem("runs.columns.1")).toBe(
      JSON.stringify(["metrics.accuracy"]),
    );
  });

  it("loadColumnsFromStorage returns fallback when missing", () => {
    expect(loadColumnsFromStorage("missing", ["a"])).toEqual(["a"]);
  });

  it("loadColumnsFromStorage returns parsed value", () => {
    localStorage.setItem("runs.columns.x", JSON.stringify(["a", "b"]));
    expect(loadColumnsFromStorage("x", ["fallback"])).toEqual(["a", "b"]);
  });
});
