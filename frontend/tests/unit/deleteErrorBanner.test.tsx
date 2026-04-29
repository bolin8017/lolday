import { type ReactNode, type ReactElement } from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { detailToDeleteBanner } from "@/components/common/deleteErrorBanner";

function renderBanner(node: ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>);
}

describe("detailToDeleteBanner", () => {
  it("returns generic 'Delete failed.' for undefined detail", () => {
    expect(detailToDeleteBanner(undefined)).toEqual({
      message: "Delete failed.",
    });
  });

  it("passes through unknown codes unchanged", () => {
    const detail = { code: "some_other_code", message: "other failure" };
    expect(detailToDeleteBanner(detail)).toEqual(detail);
  });

  it("injects link for version_has_in_flight_jobs", () => {
    const banner = detailToDeleteBanner({
      code: "version_has_in_flight_jobs",
      message: "Cancel running jobs that use this version before deleting it.",
    });
    expect(banner.code).toBe("version_has_in_flight_jobs");
    // message is now a ReactNode; render it to verify the embedded link.
    renderBanner(banner.message as ReactElement);
    expect(screen.getByText(/Cancel running jobs/i)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /See running jobs/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/jobs?status=running");
  });

  it("injects link for detector_has_in_flight_jobs", () => {
    const banner = detailToDeleteBanner({
      code: "detector_has_in_flight_jobs",
      message: "Cancel running jobs for this detector before deleting it.",
    });
    expect(banner.code).toBe("detector_has_in_flight_jobs");
    renderBanner(banner.message as ReactElement);
    expect(screen.getByText(/Cancel running jobs/i)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /See running jobs/i }),
    ).toBeInTheDocument();
  });

  it("uses fallback message text when detail.message is missing on in-flight code", () => {
    const banner = detailToDeleteBanner({ code: "version_has_in_flight_jobs" });
    renderBanner(banner.message as ReactElement);
    expect(
      screen.getByText(/Cancel running jobs first\./i),
    ).toBeInTheDocument();
  });
});
