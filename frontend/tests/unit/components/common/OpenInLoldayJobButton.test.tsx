import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { OpenInLoldayJobButton } from "@/components/common/OpenInLoldayJobButton";

function renderWithRouter(jobId: string) {
  return render(
    <MemoryRouter>
      <OpenInLoldayJobButton jobId={jobId} />
    </MemoryRouter>,
  );
}

describe("OpenInLoldayJobButton", () => {
  it("renders an anchor pointing at /jobs/<id>", () => {
    renderWithRouter("abc-123");
    const link = screen.getByRole("link", { name: /Open job/i });
    expect(link).toHaveAttribute("href", "/jobs/abc-123");
  });

  it("URL-encoded job IDs survive verbatim (react-router preserves raw segments)", () => {
    // UUID-shaped ids should never need encoding, but the consumer types
    // jobId as `string` so guard against accidental space / special-char
    // ids by pinning that the helper does NOT silently re-encode.
    renderWithRouter("with spaces");
    expect(screen.getByRole("link", { name: /Open job/i })).toHaveAttribute(
      "href",
      "/jobs/with spaces",
    );
  });
});
