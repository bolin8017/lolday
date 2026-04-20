import type { UseFormSetError, FieldValues, Path } from "react-hook-form";
import type { LoldayApiError } from "@/api/errors";

export function applyFieldErrorsToForm<T extends FieldValues>(
  err: LoldayApiError,
  setError: UseFormSetError<T>,
): void {
  for (const fe of err.fieldErrors) {
    setError(fe.field as Path<T>, { type: "server", message: fe.message });
  }
}
