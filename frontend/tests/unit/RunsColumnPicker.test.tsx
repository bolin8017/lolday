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
    expect(localStorage.getItem("lolday.runs.columns.1")).toBe(
      JSON.stringify(["metrics.accuracy"]),
    );
  });

  it("loadColumnsFromStorage returns fallback when missing", () => {
    expect(loadColumnsFromStorage("missing", ["a"])).toEqual(["a"]);
  });

  it("loadColumnsFromStorage returns parsed value", () => {
    localStorage.setItem("lolday.runs.columns.x", JSON.stringify(["a", "b"]));
    expect(loadColumnsFromStorage("x", ["fallback"])).toEqual(["a", "b"]);
  });

  it("loadColumnsFromStorage returns fallback when value is not a string array", () => {
    localStorage.setItem(
      "lolday.runs.columns.bad",
      JSON.stringify({ wrong: "shape" }),
    );
    expect(loadColumnsFromStorage("bad", ["fallback"])).toEqual(["fallback"]);
  });

  it("loadColumnsFromStorage returns fallback when JSON.parse throws", () => {
    // Covers the `catch { return fallback }` branch (L96-97) — a
    // localStorage value persisted by an older version (or hand-edited)
    // that no longer parses as JSON must not crash the page.
    localStorage.setItem("lolday.runs.columns.malformed", "not-json{[}");
    expect(loadColumnsFromStorage("malformed", ["fallback"])).toEqual([
      "fallback",
    ]);
  });

  it("calls onChange when a param is toggled", async () => {
    // Covers the params loop's `onCheckedChange={() => toggle(key)}` at
    // L70 — the existing metrics-toggle test exercises the symmetric
    // metrics branch only; without this assertion a typo in the params
    // toggle handler would silently no-op param-checkbox clicks.
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <RunsColumnPicker
        experimentId="2"
        availableMetrics={[]}
        availableParams={["lr", "batch_size"]}
        selected={["params.lr"]}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /columns/i }));
    await user.click(screen.getByText("batch_size"));
    expect(onChange).toHaveBeenCalledWith(["params.lr", "params.batch_size"]);
  });
});
