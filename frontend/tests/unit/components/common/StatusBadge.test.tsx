import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/common/StatusBadge";

/**
 * ``StatusBadge`` is the small badge used everywhere job / build status
 * surfaces (job list rows, job detail header, build progress lines, …).
 * The component combines two concerns:
 *
 * 1. ``statusTone`` → background-class lookup.
 * 2. ``useTranslation`` i18n label, falling back to the raw status string
 *    when the key is missing.
 *
 * The repo-wide vitest setup pre-loads the real ``@/i18n`` bundle, so
 * known status strings translate to their English labels in the test
 * environment.
 *
 * Behaviours covered:
 *
 * - Known status keys (succeeded / failed / running / queued_backend /
 *   cancelled) get their English label.
 * - Unknown status keys render the raw string as the label.
 * - The badge's class includes the right tone (emerald / red / sky /
 *   amber / slate) for each known status.
 * - The data-testid is always ``status-badge-<raw status>``.
 */

describe("StatusBadge", () => {
  it("renders the English label for known status keys", () => {
    render(<StatusBadge status="succeeded" />);
    // Pin the badge by testid (always uses the raw status) and assert
    // the label text via the en.json entry.
    const badge = screen.getByTestId("status-badge-succeeded");
    // en.json status.succeeded = "Succeeded"
    expect(badge.textContent).toBe("Succeeded");
  });

  it("falls back to the raw status string when no i18n key exists", () => {
    render(<StatusBadge status="bizarro_unknown_status" />);
    const badge = screen.getByTestId("status-badge-bizarro_unknown_status");
    expect(badge.textContent).toBe("bizarro_unknown_status");
  });

  it("applies the success tone class for terminal success", () => {
    render(<StatusBadge status="succeeded" />);
    expect(screen.getByTestId("status-badge-succeeded").className).toContain(
      "bg-emerald-100",
    );
  });

  it("applies the destructive tone class for failure", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByTestId("status-badge-failed").className).toContain(
      "bg-red-100",
    );
  });

  it("applies the info tone class for running", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByTestId("status-badge-running").className).toContain(
      "bg-sky-100",
    );
  });

  it("applies the warning tone class for queued_backend (Phase 6 FIFO hold)", () => {
    render(<StatusBadge status="queued_backend" />);
    expect(
      screen.getByTestId("status-badge-queued_backend").className,
    ).toContain("bg-amber-100");
  });

  it("applies the muted tone class for cancelled", () => {
    render(<StatusBadge status="cancelled" />);
    expect(screen.getByTestId("status-badge-cancelled").className).toContain(
      "bg-slate-100",
    );
  });

  it("applies the muted tone class for unknown statuses", () => {
    render(<StatusBadge status="bizarro_unknown" />);
    expect(
      screen.getByTestId("status-badge-bizarro_unknown").className,
    ).toContain("bg-slate-100");
  });

  it("renders the testid in the data-testid attribute", () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByTestId("status-badge-pending")).toBeInTheDocument();
  });
});
