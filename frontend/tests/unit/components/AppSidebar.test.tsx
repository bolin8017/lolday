import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/layout/AppSidebar";

type Role = "user" | "developer" | "admin";

const { authState } = vi.hoisted(() => ({
  authState: {
    role: "admin" as Role,
    logout: vi.fn(),
  },
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: { email: "lab@test", role: authState.role },
    isLoading: false,
    isUnauthenticated: false,
    logout: authState.logout,
  }),
}));

beforeEach(() => {
  authState.role = "admin";
  authState.logout.mockReset();
});

function renderSidebar() {
  return render(
    <MemoryRouter>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </MemoryRouter>,
  );
}

describe("AppSidebar", () => {
  it("renders the five primary nav items", () => {
    const { getByText } = renderSidebar();
    expect(getByText(/detectors|偵測器/i)).toBeInTheDocument();
    expect(getByText(/datasets|資料集/i)).toBeInTheDocument();
    expect(getByText(/jobs|工作/i)).toBeInTheDocument();
    expect(getByText(/runs|執行紀錄/i)).toBeInTheDocument();
    expect(getByText(/models|模型/i)).toBeInTheDocument();
  });

  it("renders the admin link when role is admin", () => {
    const { getByText } = renderSidebar();
    expect(getByText(/admin|管理/i)).toBeInTheDocument();
  });

  it("hides the admin link for non-admin roles", () => {
    authState.role = "developer";
    const { queryByText } = renderSidebar();
    expect(queryByText(/admin|管理/i)).toBeNull();
  });

  it("hides the admin link for plain users", () => {
    authState.role = "user";
    const { queryByText } = renderSidebar();
    expect(queryByText(/admin|管理/i)).toBeNull();
  });

  it("invokes logout when the logout button is clicked", async () => {
    const user = userEvent.setup();
    const { getByText } = renderSidebar();
    await user.click(getByText(/log\s*out|登出/i));
    expect(authState.logout).toHaveBeenCalledTimes(1);
  });
});
