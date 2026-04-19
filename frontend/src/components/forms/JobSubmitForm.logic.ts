import type { JobType } from "@/api/queries/jobs";

export function requiredFieldsForType(type: JobType): string[] {
  switch (type) {
    case "train":    return ["train_dataset_id", "test_dataset_id"];
    case "evaluate": return ["test_dataset_id", "source_model_version_id"];
    case "predict":  return ["predict_dataset_id", "source_model_version_id"];
  }
}
