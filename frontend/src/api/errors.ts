export interface ValidationFieldError {
  /** Dotted path into form, e.g., "body.email" → "email". */
  field: string;
  message: string;
}

/** Phase 13a: shape of structured `detail` objects returned by the backend
 * (e.g. `{code: "version_has_in_flight_jobs", message: "..."}` for 409
 * guards). Pre-13a backends only used array (422 validation) or string
 * detail; 13a's delete UX requires preserving the `code` so the frontend
 * can branch on it. Match `ConcurrencyLimitDetail` /
 * `version_not_active` / `version_has_in_flight_jobs` /
 * `detector_has_in_flight_jobs` shapes loosely — extra keys (e.g.
 * `limit`, `in_flight`) get carried in `extra`. */
export interface StructuredDetail {
  code?: string;
  message?: string;
  extra?: Record<string, unknown>;
}

export class LoldayApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly fieldErrors: ValidationFieldError[];
  readonly structuredDetail?: StructuredDetail;

  constructor(
    status: number,
    detail: string,
    fieldErrors: ValidationFieldError[] = [],
    structuredDetail?: StructuredDetail,
  ) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
    this.structuredDetail = structuredDetail;
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
    if (typeof detail === "object" && detail !== null) {
      // Phase 13a: object-shaped detail like {code, message} from 409 guards.
      // Preserve the structured form so callers can branch on `code`; pre-13a
      // string `detail` field stays informational (set to message).
      const d = detail as Record<string, unknown>;
      const code = typeof d.code === "string" ? d.code : undefined;
      const message = typeof d.message === "string" ? d.message : undefined;
      const known = new Set(["code", "message"]);
      const extra: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(d)) {
        if (!known.has(k)) extra[k] = v;
      }
      return new LoldayApiError(
        status,
        message ?? code ?? `HTTP ${status}`,
        [],
        { code, message, extra: Object.keys(extra).length ? extra : undefined },
      );
    }
  }
  return new LoldayApiError(status, `HTTP ${status}`);
}
