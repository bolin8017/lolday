import { describe, it, expect } from "vitest";
import { formatDuration, formatRelative } from "@/lib/date";

describe("formatDuration", () => {
  it("returns em-dash for null/undefined", () => {
    expect(formatDuration(null, null)).toBe("—");
    expect(formatDuration("2026-01-01T00:00:00Z", null)).toBe("—");
  });

  it("formats seconds under a minute", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe(
      "45s",
    );
  });

  it("formats minutes + seconds", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:02:03Z")).toBe(
      "2m 3s",
    );
  });

  it("formats hours + minutes", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")).toBe(
      "1h 30m",
    );
  });
});

describe("formatRelative", () => {
  it("handles recent", () => {
    const now = new Date();
    const tenSecAgo = new Date(now.getTime() - 10_000).toISOString();
    expect(formatRelative(tenSecAgo)).toMatch(/seconds ago/);
  });

  it("returns em-dash for null/undefined input", () => {
    // The job-detail UI passes column values straight in; nullable
    // timestamps would crash through `new Date(null)` if the guard
    // were dropped.
    expect(formatRelative(null)).toBe("—");
    expect(formatRelative(undefined)).toBe("—");
    expect(formatRelative("")).toBe("—");
  });

  it("delegates to date-fns formatDistanceToNow for timestamps older than 60s", () => {
    // The `<60s` branch returns "<n> seconds ago" verbatim; anything
    // older falls through to formatDistanceToNow which produces
    // "about 5 minutes ago" / "1 hour ago" — assert the suffix `ago`
    // and that the seconds-ago prefix does NOT appear.
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    const out = formatRelative(fiveMinAgo);
    expect(out).toMatch(/ago/);
    expect(out).not.toMatch(/seconds ago/);
  });
});

// ----- formatDuration edge cases -----

describe("formatDuration edge cases", () => {
  it("clamps a backwards interval (end < start) to 0s", () => {
    // Clock skew between two pods can produce end_at < start_at; the
    // `Math.max(0, …)` clamp prevents a negative-second display like
    // "-3s".
    expect(formatDuration("2026-01-01T00:00:10Z", "2026-01-01T00:00:00Z")).toBe(
      "0s",
    );
  });

  it("returns em-dash when only end is provided", () => {
    // The earlier test covers null-end + start-only; pin the
    // symmetric guard against null-start + end-only.
    expect(formatDuration(null, "2026-01-01T00:00:45Z")).toBe("—");
    expect(formatDuration(undefined, "2026-01-01T00:00:45Z")).toBe("—");
  });

  it("formats exactly one minute as '1m 0s'", () => {
    // Boundary case at the seconds → minutes branch (secs === 60).
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")).toBe(
      "1m 0s",
    );
  });

  it("formats exactly one hour as '1h 0m'", () => {
    // Boundary case at the minutes → hours branch (secs === 3600).
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")).toBe(
      "1h 0m",
    );
  });
});
