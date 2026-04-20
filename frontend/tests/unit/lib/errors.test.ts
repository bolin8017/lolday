import { describe, it, expect, vi } from "vitest";
import { LoldayApiError } from "@/api/errors";
import { applyFieldErrorsToForm } from "@/lib/errors";

describe("applyFieldErrorsToForm", () => {
  it("calls setError for each field error", () => {
    const setError = vi.fn();
    const err = new LoldayApiError(422, "Validation failed", [
      { field: "email", message: "Not a valid email" },
      { field: "password", message: "Too short" },
    ]);
    applyFieldErrorsToForm(err, setError as any);
    expect(setError).toHaveBeenCalledTimes(2);
    expect(setError).toHaveBeenCalledWith("email", { type: "server", message: "Not a valid email" });
  });
});
