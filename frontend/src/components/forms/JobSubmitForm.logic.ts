import type { JobType } from "@/api/queries/jobs";

export function requiredFieldsForType(type: JobType): string[] {
  switch (type) {
    case "train":    return ["train_dataset_id", "test_dataset_id"];
    case "evaluate": return ["test_dataset_id", "source_model_version_id"];
    case "predict":  return ["predict_dataset_id", "source_model_version_id"];
    default:         return [];
  }
}

export type ParseParamsResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

export function parseParams(text: string): ParseParamsResult {
  if (!text.trim()) return { ok: true, value: {} };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "params must be a JSON object" };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}
