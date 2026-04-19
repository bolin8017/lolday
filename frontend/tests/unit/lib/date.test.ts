import { describe, it, expect } from "vitest";
import { formatDuration, formatRelative } from "@/lib/date";

describe("formatDuration", () => {
  it("returns em-dash for null/undefined", () => {
    expect(formatDuration(null, null)).toBe("—");
    expect(formatDuration("2026-01-01T00:00:00Z", null)).toBe("—");
  });

  it("formats seconds under a minute", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe("45s");
  });

  it("formats minutes + seconds", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:02:03Z")).toBe("2m 3s");
  });

  it("formats hours + minutes", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")).toBe("1h 30m");
  });
});

describe("formatRelative", () => {
  it("handles recent", () => {
    const now = new Date();
    const tenSecAgo = new Date(now.getTime() - 10_000).toISOString();
    expect(formatRelative(tenSecAgo)).toMatch(/seconds ago/);
  });
});
