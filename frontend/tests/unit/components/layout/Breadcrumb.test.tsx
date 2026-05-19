import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { Breadcrumb } from "@/components/layout/Breadcrumb";

/**
 * `Breadcrumb` drives the secondary navigation in `TopBar`. It reads from
 * `useBreadcrumb` and renders:
 *
 * - Nothing at all when the hook returns an empty array (avoids an empty
 *   `<nav>` taking layout space on routes that don't define crumbs).
 * - A `<Link>` for every crumb except the last (the current page is a
 *   non-clickable span so a user can't navigate "to here").
 * - A `ChevronRight` separator BETWEEN crumbs (`i > 0`), not before the
 *   first one — otherwise the bar leads with a chevron.
 *
 * The hook is mocked per-test so each rendering path is exercised without
 * spinning up a full route table.
 */

vi.mock("@/hooks/useBreadcrumb", () => ({
  useBreadcrumb: vi.fn(),
}));

import { useBreadcrumb } from "@/hooks/useBreadcrumb";

const mockedUseBreadcrumb = vi.mocked(useBreadcrumb);

function renderBreadcrumb(crumbs: Array<{ pathname: string; label: string }>) {
  mockedUseBreadcrumb.mockReturnValue(crumbs);
  return render(
    <MemoryRouter>
      <Breadcrumb />
    </MemoryRouter>,
  );
}

describe("Breadcrumb", () => {
  it("renders nothing when there are no crumbs", () => {
    const { container } = renderBreadcrumb([]);
    expect(container.firstChild).toBeNull();
  });

  it("renders the single-crumb case as a plain (non-link) span", () => {
    renderBreadcrumb([{ pathname: "/jobs", label: "Jobs" }]);
    expect(screen.getByText("Jobs")).toBeInTheDocument();
    // No <a>: the current page must not be a clickable target.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders intermediate crumbs as links and the last one as a span", () => {
    renderBreadcrumb([
      { pathname: "/jobs", label: "Jobs" },
      { pathname: "/jobs/abc", label: "Job abc" },
    ]);
    const link = screen.getByRole("link", { name: "Jobs" });
    expect(link).toHaveAttribute("href", "/jobs");
    // The leaf crumb is a span with the current-page colour class
    // (`text-foreground`), not an anchor.
    expect(
      screen.queryByRole("link", { name: "Job abc" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Job abc")).toBeInTheDocument();
  });

  it("inserts a separator between crumbs but not before the first", () => {
    const { container } = renderBreadcrumb([
      { pathname: "/jobs", label: "Jobs" },
      { pathname: "/jobs/abc", label: "Job abc" },
      { pathname: "/jobs/abc/logs", label: "Logs" },
    ]);
    // Three crumbs -> two separators (lucide ChevronRight renders an
    // <svg>). The Separator in TopBar is outside the component.
    const svgs = container.querySelectorAll("svg");
    expect(svgs.length).toBe(2);
  });
});
