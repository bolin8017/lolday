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

  it.each([
    {
      label: /^dark$|^深色$/i,
      key: "dark",
      expectedClass: "dark",
    },
    {
      label: /^light$|^淺色$/i,
      key: "light",
      expectedClass: "light",
    },
  ])(
    "clicking '$key' applies '$expectedClass' class to <html>",
    async ({ label, expectedClass }) => {
      const user = userEvent.setup();
      const { getByLabelText } = render(
        <ThemeProvider defaultTheme="system">
          <ThemeToggle />
        </ThemeProvider>,
      );
      await user.click(getByLabelText(/toggle theme|切換主題/i));
      const item = await within(document.body).findByText(label);
      await user.click(item);
      expect(document.documentElement.classList.contains(expectedClass)).toBe(
        true,
      );
    },
  );

  it("clicking 'system' resolves to either light or dark via prefers-color-scheme", async () => {
    const user = userEvent.setup();
    const { getByLabelText } = render(
      <ThemeProvider defaultTheme="light">
        <ThemeToggle />
      </ThemeProvider>,
    );
    await user.click(getByLabelText(/toggle theme|切換主題/i));
    const item = await within(document.body).findByText(/^system$|^跟隨系統$/i);
    await user.click(item);
    // System mode reads matchMedia; with the default matchMedia stub
    // returning matches:false, system resolves to "light".
    expect(
      document.documentElement.classList.contains("light") ||
        document.documentElement.classList.contains("dark"),
    ).toBe(true);
  });
});
