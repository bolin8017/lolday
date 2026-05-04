import { render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach } from "vitest";
import { ThemeProvider } from "@/components/ThemeProvider";
import { ThemeToggle } from "@/components/ThemeToggle";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("light", "dark");
});

describe("ThemeToggle", () => {
  it("renders three theme options when opened", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(getByLabelText(/toggle theme|切換主題/i));
    const menu = await within(document.body).findByRole("menu");
    expect(within(menu).getByText(/light|淺色/i)).toBeInTheDocument();
    expect(within(menu).getByText(/dark|深色/i)).toBeInTheDocument();
    expect(within(menu).getByText(/system|跟隨系統/i)).toBeInTheDocument();
  });

  it("clicking 'Dark' adds the dark class to <html>", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(getByLabelText(/toggle theme|切換主題/i));
    const dark = await within(document.body).findByText(/^dark$|^深色$/i);
    await user.click(dark);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
