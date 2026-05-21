import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JsonTreeView } from "@/components/common/JsonTreeView";

// react-json-view-lite ships two built-in StyleProps; we forward the active
// theme as a data-attribute on the wrapper for cheap visual-mode assertions.
// The library applies CSS-module class names that differ per theme, but those
// hashes are not stable enough to assert against directly.

describe("JsonTreeView", () => {
  afterEach(() => {
    document.documentElement.classList.remove("light", "dark");
    vi.restoreAllMocks();
  });

  it("renders the JSON content with the root key visible at level 0", async () => {
    await act(async () => {
      render(<JsonTreeView value={{ alpha: 1, beta: { nested: 2 } }} />);
    });
    const tree = screen.getByTestId("json-tree-view");
    // Library renders keys as "alpha:" (key + colon) in one span, so we use
    // a regex matcher rather than an exact string match.
    expect(within(tree).getByText(/^alpha:?$/)).toBeInTheDocument();
    expect(within(tree).getByText(/^beta:?$/)).toBeInTheDocument();
  });

  it("collapses nested objects when collapsed=1 (default)", async () => {
    await act(async () => {
      render(<JsonTreeView value={{ alpha: { hidden: 42 } }} />);
    });
    const tree = screen.getByTestId("json-tree-view");
    // Root-level key is visible; nested key is collapsed away.
    expect(within(tree).getByText(/^alpha:?$/)).toBeInTheDocument();
    expect(within(tree).queryByText(/^hidden:?$/)).not.toBeInTheDocument();
  });

  it("expands every level when collapsed=false", async () => {
    await act(async () => {
      render(
        <JsonTreeView value={{ alpha: { hidden: 42 } }} collapsed={false} />,
      );
    });
    const tree = screen.getByTestId("json-tree-view");
    expect(within(tree).getByText(/^hidden:?$/)).toBeInTheDocument();
  });

  it("collapses every level when collapsed=true", async () => {
    await act(async () => {
      render(
        <JsonTreeView value={{ alpha: { hidden: 42 } }} collapsed={true} />,
      );
    });
    const tree = screen.getByTestId("json-tree-view");
    // Even root level is collapsed; the only visible content is the ellipsis.
    expect(within(tree).queryByText(/^alpha:?$/)).not.toBeInTheDocument();
    expect(within(tree).queryByText(/^hidden:?$/)).not.toBeInTheDocument();
  });

  it("reports the resolved theme as a data attribute when dark", async () => {
    document.documentElement.classList.add("dark");
    await act(async () => {
      render(<JsonTreeView value={{ a: 1 }} />);
    });
    expect(screen.getByTestId("json-tree-view").dataset.theme).toBe("dark");
  });

  it("reports the resolved theme as a data attribute when light", async () => {
    document.documentElement.classList.add("light");
    await act(async () => {
      render(<JsonTreeView value={{ a: 1 }} />);
    });
    expect(screen.getByTestId("json-tree-view").dataset.theme).toBe("light");
  });

  it("renders the copy button by default and writes JSON to the clipboard", async () => {
    // userEvent.setup() installs its own emulated navigator.clipboard, so we
    // spy on it AFTER setup to capture the writeText call from our handler.
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    await act(async () => {
      render(<JsonTreeView value={{ a: 1 }} />);
    });
    const button = screen.getByRole("button", {
      name: /Copy JSON to clipboard/,
    });
    await act(async () => {
      await user.click(button);
    });
    expect(writeText).toHaveBeenCalledWith(JSON.stringify({ a: 1 }, null, 2));
    expect(screen.getByRole("button", { name: /Copied/ })).toBeInTheDocument();
  });

  it("hides the copy button when copyable=false", async () => {
    await act(async () => {
      render(<JsonTreeView value={{ a: 1 }} copyable={false} />);
    });
    expect(
      screen.queryByRole("button", { name: /Copy JSON to clipboard/ }),
    ).not.toBeInTheDocument();
  });

  it("falls back to an empty object when value is null", async () => {
    await act(async () => {
      render(<JsonTreeView value={null} />);
    });
    expect(screen.getByTestId("json-tree-view")).toBeInTheDocument();
  });

  it("copy click is a no-op when navigator.clipboard is unavailable", async () => {
    // Pins L38 `if (!navigator.clipboard) return;` — older Safari and a
    // few embedded browsers ship without the Clipboard API; the handler
    // must not crash (no `await null.writeText(...)` TypeError) and the
    // "Copied" UI state must NOT advance.
    const user = userEvent.setup();
    const origClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    try {
      await act(async () => {
        render(<JsonTreeView value={{ a: 1 }} />);
      });
      const button = screen.getByRole("button", {
        name: /Copy JSON to clipboard/,
      });
      // Must not throw, must not advance to "Copied" since the handler
      // returned early without setting copied=true.
      await act(async () => {
        await user.click(button);
      });
      expect(
        screen.queryByRole("button", { name: /Copied/ }),
      ).not.toBeInTheDocument();
    } finally {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: origClipboard,
      });
    }
  });
});
