import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { detailToDeleteBanner } from "@/components/common/deleteErrorBanner";

/**
 * ``detailToDeleteBanner`` adapts a backend ``HTTPException.detail``
 * payload into the banner shape rendered by ``_authed.detectors._index.tsx``
 * and ``_authed.detectors.$id.tsx`` on delete failures.
 *
 * Three branches to pin:
 *
 * 1. ``detail`` is ``undefined`` → static "Delete failed." message,
 *    no code.
 * 2. ``detail.code`` is one of the in-flight-jobs codes
 *    (``version_has_in_flight_jobs`` / ``detector_has_in_flight_jobs``)
 *    → enriched message ending in a "See running jobs" link that
 *    navigates to ``/jobs?status=running``.
 * 3. Any other detail → pass-through unchanged.
 */

function renderBanner(message: React.ReactNode) {
  // The in-flight branch returns a React fragment containing a Link.
  // Wrap with MemoryRouter so the Link renders.
  return render(<MemoryRouter>{message}</MemoryRouter>);
}

describe("detailToDeleteBanner", () => {
  it("returns the static 'Delete failed.' message when detail is undefined", () => {
    const banner = detailToDeleteBanner(undefined);
    expect(banner.code).toBeUndefined();
    expect(banner.message).toBe("Delete failed.");
  });

  it("passes a detail through unchanged when the code is not in-flight", () => {
    const banner = detailToDeleteBanner({
      code: "some_other_code",
      message: "Custom message.",
    });
    expect(banner.code).toBe("some_other_code");
    expect(banner.message).toBe("Custom message.");
  });

  it("enriches version_has_in_flight_jobs detail with a 'See running jobs' link", () => {
    const banner = detailToDeleteBanner({
      code: "version_has_in_flight_jobs",
      message: "Cancel running jobs first.",
    });
    expect(banner.code).toBe("version_has_in_flight_jobs");
    renderBanner(banner.message);
    expect(screen.getByText(/Cancel running jobs first\./)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /See running jobs/ });
    expect(link).toHaveAttribute("href", "/jobs?status=running");
  });

  it("enriches detector_has_in_flight_jobs detail with the same link", () => {
    const banner = detailToDeleteBanner({
      code: "detector_has_in_flight_jobs",
      message: "Detector has running jobs.",
    });
    expect(banner.code).toBe("detector_has_in_flight_jobs");
    renderBanner(banner.message);
    expect(screen.getByText(/Detector has running jobs\./)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /See running jobs/ }),
    ).toHaveAttribute("href", "/jobs?status=running");
  });

  it("falls back to a default phrase when the in-flight detail has no message", () => {
    const banner = detailToDeleteBanner({ code: "version_has_in_flight_jobs" });
    renderBanner(banner.message);
    expect(screen.getByText(/Cancel running jobs first\./)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /See running jobs/ }),
    ).toBeInTheDocument();
  });
});
