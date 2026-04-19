import { describe, it, expect } from "vitest";
import { statusTone, isTerminal, NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

describe("statusTone", () => {
  it("maps success-ish statuses to success", () => {
    expect(statusTone("succeeded")).toBe("success");
    expect(statusTone("success")).toBe("success");
  });
  it("maps failed to destructive", () => {
    expect(statusTone("failed")).toBe("destructive");
    expect(statusTone("timeout")).toBe("destructive");
  });
  it("maps running to info", () => {
    expect(statusTone("running")).toBe("info");
    expect(statusTone("scanning")).toBe("info");
  });
  it("maps pending to muted", () => {
    expect(statusTone("pending")).toBe("muted");
  });
});

describe("isTerminal", () => {
  it("returns false for running-ish statuses", () => {
    for (const s of NON_TERMINAL_JOB_STATUSES) expect(isTerminal(s)).toBe(false);
  });
  it("returns true for succeeded / failed / cancelled / timeout", () => {
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("cancelled")).toBe(true);
    expect(isTerminal("timeout")).toBe(true);
  });
});
