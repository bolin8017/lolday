import { describe, it, expect } from "vitest";
import { parseError, LoldayApiError } from "@/api/errors";

describe("parseError", () => {
  it("parses validation array detail (422)", () => {
    const err = parseError(422, {
      detail: [
        { loc: ["body", "email"], msg: "field required" },
        { loc: ["body", "params", "lr"], msg: "must be > 0" },
      ],
    });
    expect(err.status).toBe(422);
    expect(err.detail).toBe("Validation failed");
    expect(err.fieldErrors).toEqual([
      { field: "email", message: "field required" },
      { field: "params.lr", message: "must be > 0" },
    ]);
    expect(err.structuredDetail).toBeUndefined();
  });

  it("parses string detail (legacy / 404 bare-string)", () => {
    const err = parseError(404, { detail: "version not found" });
    expect(err.status).toBe(404);
    expect(err.detail).toBe("version not found");
    expect(err.structuredDetail).toBeUndefined();
  });

  it("parses object detail with code + message (Phase 13a 409 in-flight)", () => {
    const err = parseError(409, {
      detail: {
        code: "version_has_in_flight_jobs",
        message: "Cancel running jobs that use this version before deleting it.",
      },
    });
    expect(err.status).toBe(409);
    expect(err.structuredDetail).toEqual({
      code: "version_has_in_flight_jobs",
      message: "Cancel running jobs that use this version before deleting it.",
      extra: undefined,
    });
    // .detail is the human-readable string (message), not "HTTP 409"
    expect(err.detail).toBe("Cancel running jobs that use this version before deleting it.");
  });

  it("parses object detail with code only (no message)", () => {
    const err = parseError(409, { detail: { code: "version_not_active" } });
    expect(err.structuredDetail?.code).toBe("version_not_active");
    expect(err.structuredDetail?.message).toBeUndefined();
    expect(err.detail).toBe("version_not_active");
  });

  it("preserves extra keys on object detail (e.g. ConcurrencyLimitDetail)", () => {
    const err = parseError(429, {
      detail: {
        code: "concurrency_limit",
        message: "too many in-flight builds",
        limit: 10,
        in_flight: 12,
      },
    });
    expect(err.structuredDetail?.extra).toEqual({ limit: 10, in_flight: 12 });
  });

  it("falls back to HTTP <status> when body is unstructured", () => {
    const err = parseError(500, "Internal Server Error");
    expect(err.detail).toBe("HTTP 500");
    expect(err.structuredDetail).toBeUndefined();
  });

  it("falls back when body has no detail key", () => {
    const err = parseError(500, { foo: "bar" });
    expect(err.detail).toBe("HTTP 500");
    expect(err.structuredDetail).toBeUndefined();
  });
});

describe("LoldayApiError", () => {
  it("Error.message uses detail when available", () => {
    const err = new LoldayApiError(409, "Cancel running jobs first.");
    expect(err.message).toBe("Cancel running jobs first.");
  });

  it("Error.message falls back to HTTP <status>", () => {
    const err = new LoldayApiError(500, "");
    expect(err.message).toBe("HTTP 500");
  });

  it("structuredDetail is preserved when constructed with one", () => {
    const err = new LoldayApiError(409, "msg", [], { code: "x", message: "msg" });
    expect(err.structuredDetail).toEqual({ code: "x", message: "msg" });
  });
});
