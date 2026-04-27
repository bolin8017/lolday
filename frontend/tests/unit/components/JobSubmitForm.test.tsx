import { describe, it, expect } from "vitest";
import {
  requiredFieldsForType,
  parseParams,
} from "@/components/forms/JobSubmitForm.logic";

describe("requiredFieldsForType", () => {
  it("train needs train+test datasets", () => {
    expect(requiredFieldsForType("train")).toEqual(["train_dataset_id", "test_dataset_id"]);
  });
  it("evaluate needs test+source_model", () => {
    expect(requiredFieldsForType("evaluate")).toEqual(["test_dataset_id", "source_model_version_id"]);
  });
  it("predict needs predict+source_model", () => {
    expect(requiredFieldsForType("predict")).toEqual(["predict_dataset_id", "source_model_version_id"]);
  });
});

describe("parseParams", () => {
  it("returns empty object for blank text", () => {
    expect(parseParams("")).toEqual({ ok: true, value: {} });
    expect(parseParams("   \n  \t  ")).toEqual({ ok: true, value: {} });
  });

  it("parses a valid JSON object", () => {
    expect(parseParams('{"epochs": 5, "lr": 0.01}')).toEqual({
      ok: true,
      value: { epochs: 5, lr: 0.01 },
    });
  });

  it("rejects JSON arrays / primitives at the top level", () => {
    const arrResult = parseParams("[1, 2, 3]");
    expect(arrResult.ok).toBe(false);
    if (!arrResult.ok) expect(arrResult.error).toMatch(/object/i);

    const numResult = parseParams("42");
    expect(numResult.ok).toBe(false);
  });

  it("rejects invalid JSON", () => {
    const result = parseParams("{not json}");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.length).toBeGreaterThan(0);
  });
});
