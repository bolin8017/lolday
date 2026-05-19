import { describe, it, expect } from "vitest";
import {
  statusTone,
  isTerminal,
  NON_TERMINAL_JOB_STATUSES,
  NON_TERMINAL_BUILD_STATUSES,
} from "@/lib/status";

describe("statusTone", () => {
  it("maps success-ish statuses to success", () => {
    expect(statusTone("succeeded")).toBe("success");
    expect(statusTone("success")).toBe("success");
  });
  it("maps failed to destructive", () => {
    expect(statusTone("failed")).toBe("destructive");
    expect(statusTone("timeout")).toBe("destructive");
  });
  it("maps running to info", () => {
    expect(statusTone("running")).toBe("info");
    expect(statusTone("scanning")).toBe("info");
  });
  it("maps pending to muted", () => {
    expect(statusTone("pending")).toBe("muted");
  });

  // ----- Branch coverage additions -----

  it("maps cancelled to muted", () => {
    // cancelled is reachable both as a terminal job status and a manual
    // build cancel — the tone must match pending so the chart row reads
    // as 'not running, no action needed' rather than 'failed'.
    expect(statusTone("cancelled")).toBe("muted");
  });

  it("maps build-only running-ish statuses (building, preparing) to info", () => {
    // Without these the build-detail UI would default to "muted" for an
    // in-flight build — silently swapping the in-progress indicator for
    // the queued one.
    expect(statusTone("building")).toBe("info");
    expect(statusTone("preparing")).toBe("info");
  });

  it("maps Phase 6 queued_backend to warning", () => {
    // queued_backend is the backend-FIFO holding state introduced in
    // Phase 6 (see docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md).
    // Its tone is intentionally distinct from `pending` — a queued_backend
    // job has cleared submission validation but is held by the backend
    // anti-starvation scheduler, which is worth flagging visually so an
    // operator can drill in without confusing it for `pending` (Volcano
    // queue admission).
    expect(statusTone("queued_backend")).toBe("warning");
  });

  it("falls back to muted for unknown statuses", () => {
    // Defends against a future backend that adds a new job status without
    // a corresponding frontend TONE_MAP entry — fall back to muted rather
    // than crash on an undefined `Tone` value.
    expect(statusTone("totally-new-status-that-does-not-exist")).toBe("muted");
    expect(statusTone("")).toBe("muted");
  });
});

describe("isTerminal", () => {
  it("returns false for running-ish statuses", () => {
    for (const s of NON_TERMINAL_JOB_STATUSES)
      expect(isTerminal(s)).toBe(false);
  });
  it("returns true for succeeded / failed / cancelled / timeout", () => {
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("cancelled")).toBe(true);
    expect(isTerminal("timeout")).toBe(true);
  });
  it("returns false for non-terminal build statuses (building / scanning)", () => {
    // The helper is shared between job-status and build-status views; a
    // build that's still in `building` or `scanning` must NOT be flagged
    // as terminal or the UI hides its progress bar.
    for (const s of NON_TERMINAL_BUILD_STATUSES)
      expect(isTerminal(s)).toBe(false);
  });
});
