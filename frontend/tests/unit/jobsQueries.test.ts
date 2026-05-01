import { describe, it, expect } from "vitest";
import { isJobType, JOB_TYPES } from "@/api/queries/jobs";

describe("isJobType", () => {
  it("accepts the three known job types", () => {
    for (const t of JOB_TYPES) {
      expect(isJobType(t)).toBe(true);
    }
  });

  it("rejects unknown strings", () => {
    expect(isJobType("finetune")).toBe(false);
    expect(isJobType("")).toBe(false);
    expect(isJobType("Train")).toBe(false); // case-sensitive
  });

  it("rejects non-string values", () => {
    expect(isJobType(undefined)).toBe(false);
    expect(isJobType(null)).toBe(false);
    expect(isJobType(0)).toBe(false);
    expect(isJobType({})).toBe(false);
  });

  it("narrows the type at compile time", () => {
    const v: unknown = "predict";
    if (isJobType(v)) {
      // v is now JobType — these field reads must compile.
      const known: "train" | "evaluate" | "predict" = v;
      expect(known).toBe("predict");
    }
  });
});
