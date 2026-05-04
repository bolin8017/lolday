import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/layout/AppSidebar";

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: { email: "lab@test", role: "admin" },
    isLoading: false,
    isUnauthenticated: false,
    logout: vi.fn(),
  }),
}));

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
});
