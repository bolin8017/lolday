import { describe, it, expect } from "vitest";
import { requiredFieldsForType } from "@/components/forms/JobSubmitForm.logic";

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
