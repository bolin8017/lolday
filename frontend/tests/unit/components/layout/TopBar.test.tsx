/**
 * Render smoke for the AuthedLayout TopBar shell.
 *
 * Coverage gap closed: TopBar.tsx was at 0% because every higher-level
 * test that touches the AuthedLayout mocks both AppSidebar and TopBar
 * (see tests/unit/routes/AuthedLayout.test.tsx). With no direct test,
 * a typo in the header markup (missing trigger / breadcrumb / theme
 * toggle slot) would land without surfacing a CI signal. This test
 * mounts the real component and asserts each of its three composed
 * widgets is in the DOM.
 */
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/ThemeProvider";
import { TopBar } from "@/components/layout/TopBar";

// Breadcrumb reads from `useMatches()` (data-router-only API). MemoryRouter
// is a non-data router, so we mock the breadcrumb hook directly — same
// approach as `tests/unit/components/layout/Breadcrumb.test.tsx`.
vi.mock("@/hooks/useBreadcrumb", () => ({
  useBreadcrumb: () => [],
}));

function renderTopBar() {
  return render(
    <MemoryRouter>
      <ThemeProvider defaultTheme="light">
        <SidebarProvider>
          <TopBar />
        </SidebarProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("TopBar", () => {
  it("renders the header landmark with the sidebar trigger + theme toggle", () => {
    const { container, getByText, getByLabelText } = renderTopBar();

    // <header> landmark — drops to 0 instances if the JSX shell is removed.
    const headers = container.querySelectorAll("header");
    expect(headers.length).toBe(1);

    // SidebarTrigger renders the upstream shadcn sr-only "Toggle Sidebar"
    // label inside the button (no aria-label).
    expect(getByText("Toggle Sidebar")).toBeInTheDocument();

    // ThemeToggle button carries its own aria-label via i18n
    // ("toggle theme" / "切換主題").
    expect(getByLabelText(/toggle theme|切換主題/i)).toBeInTheDocument();
  });
});
