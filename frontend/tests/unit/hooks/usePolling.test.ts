import { describe, it, expect } from "vitest";
import { computePollInterval } from "@/hooks/usePolling";

describe("computePollInterval", () => {
  it("returns interval when predicate says active", () => {
    expect(computePollInterval(true, 2000)).toBe(2000);
  });
  it("returns false when inactive", () => {
    expect(computePollInterval(false, 2000)).toBe(false);
  });
  it("handles undefined data safely", () => {
    expect(computePollInterval(undefined, 2000)).toBe(false);
  });
});
