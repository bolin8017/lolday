import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import AuthedLayout from "@/routes/_authed";

type AuthState = {
  isLoading: boolean;
  isUnauthenticated: boolean;
  currentUser: { email: string; role: "admin" | "developer" | "user" } | null;
};

const { authState } = vi.hoisted(() => ({
  authState: {
    isLoading: false,
    isUnauthenticated: false,
    currentUser: { email: "lab@test", role: "admin" },
  } as AuthState,
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: authState.currentUser,
    isLoading: authState.isLoading,
    isUnauthenticated: authState.isUnauthenticated,
    logout: vi.fn(),
  }),
}));

// Stub the heavy layout children so the test exercises only the auth-gate
// branches in `_authed.tsx`. AppSidebar + TopBar each pull TanStack Query
// hooks (auth probe, GPU status, role list, …) and exhaustively mocking
// those is out of scope for a route-shell test.
vi.mock("@/components/layout/AppSidebar", () => ({
  AppSidebar: () => <aside data-testid="stub-sidebar" />,
}));
vi.mock("@/components/layout/TopBar", () => ({
  TopBar: () => <header data-testid="stub-topbar" />,
}));
vi.mock("@/components/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

beforeEach(() => {
  authState.isLoading = false;
  authState.isUnauthenticated = false;
  authState.currentUser = { email: "lab@test", role: "admin" };
});

function renderAuthed(
  child: React.ReactNode = <div data-testid="outlet-stub" />,
) {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<AuthedLayout />}>
          <Route index element={child} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("_authed.tsx (AuthedLayout)", () => {
  it("renders the Loading… branch while useAuth.isLoading is true", () => {
    authState.isLoading = true;
    authState.currentUser = null;
    const { getByText, queryByTestId } = renderAuthed();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    // Neither diagnostic nor the sidebar layout should mount.
    expect(queryByTestId("stub-sidebar")).toBeNull();
    expect(queryByTestId("outlet-stub")).toBeNull();
  });

  it("renders the Session-not-established diagnostic when unauthenticated", () => {
    authState.isUnauthenticated = true;
    authState.currentUser = null;
    const { getByText, getByRole, queryByTestId } = renderAuthed();
    expect(getByText(/Session not established/)).toBeInTheDocument();
    // The diagnostic carries an operator-facing reason that explains
    // why the user is here without a CF Access JWT.
    expect(getByText(/Cloudflare Access JWT/)).toBeInTheDocument();
    // One-click re-auth path — must go through cloudflared's logout
    // route to clear the bad session before retrying.
    const link = getByRole("link", { name: /Sign in via Cloudflare Access/ });
    expect(link).toHaveAttribute("href", "/cdn-cgi/access/logout");
    expect(queryByTestId("stub-sidebar")).toBeNull();
  });

  it("renders the diagnostic when isUnauthenticated is false but currentUser is null", () => {
    // Defensive guard in the source: `isUnauthenticated || !currentUser`
    // covers the edge case where the auth probe resolves with no error
    // but also no user payload.
    authState.isUnauthenticated = false;
    authState.currentUser = null;
    const { getByText } = renderAuthed();
    expect(getByText(/Session not established/)).toBeInTheDocument();
  });

  it("renders the sidebar layout + outlet when authenticated", () => {
    authState.isLoading = false;
    authState.isUnauthenticated = false;
    authState.currentUser = { email: "lab@test", role: "admin" };
    const { getByTestId, queryByText } = renderAuthed(
      <div data-testid="outlet-stub">child route content</div>,
    );
    expect(getByTestId("stub-sidebar")).toBeInTheDocument();
    expect(getByTestId("stub-topbar")).toBeInTheDocument();
    expect(getByTestId("outlet-stub")).toBeInTheDocument();
    expect(queryByText(/Session not established/)).toBeNull();
    expect(queryByText(/Loading…/)).toBeNull();
  });
});
