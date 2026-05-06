import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ModelsListPage from "@/routes/_authed.models._index";

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  // Pre-seed an empty list so the table renders immediately
  qc.setQueryData(["models", "list"], []);
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ModelsListPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ModelsListPage stage explainer", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the explainer Alert on first visit", () => {
    renderPage();
    expect(screen.getByText(/About model stages/)).toBeInTheDocument();
  });

  it("hides the explainer after Dismiss is clicked", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    expect(screen.queryByText(/About model stages/)).toBeNull();
    expect(localStorage.getItem("lolday.modelsExplainerDismissed")).toBe("1");
  });

  it("does not render the explainer when localStorage flag is set", () => {
    localStorage.setItem("lolday.modelsExplainerDismissed", "1");
    renderPage();
    expect(screen.queryByText(/About model stages/)).toBeNull();
  });
});
