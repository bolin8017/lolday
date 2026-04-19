import { describe, it, expect } from "vitest";
import { cellColor } from "@/components/charts/ConfusionMatrix";

describe("ConfusionMatrix cellColor", () => {
  it("returns success tone for diagonal", () => {
    expect(cellColor(0, 0, true)).toBe("success");
    expect(cellColor(1, 1, true)).toBe("success");
  });
  it("returns warn for off-diagonal", () => {
    expect(cellColor(0, 1, false)).toBe("warn");
  });
});
