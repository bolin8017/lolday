export interface ValidationFieldError {
  /** Dotted path into form, e.g., "body.email" → "email". */
  field: string;
  message: string;
}

export class LoldayApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly fieldErrors: ValidationFieldError[];

  constructor(status: number, detail: string, fieldErrors: ValidationFieldError[] = []) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
  }
}

type RawValidationItem = { loc: (string | number)[]; msg: string };

export function parseError(status: number, body: unknown): LoldayApiError {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (Array.isArray(detail)) {
      const fieldErrors: ValidationFieldError[] = detail
        .filter((d): d is RawValidationItem =>
          typeof d === "object" && d !== null && "loc" in d && "msg" in d)
        .map((d) => ({
          field: d.loc.filter((p) => p !== "body").join("."),
          message: d.msg,
        }));
      return new LoldayApiError(status, "Validation failed", fieldErrors);
    }
    if (typeof detail === "string") {
      return new LoldayApiError(status, detail);
    }
  }
  return new LoldayApiError(status, `HTTP ${status}`);
}
