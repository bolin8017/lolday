import { render, act } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { JsonTreeView } from "@/components/common/JsonTreeView";

// react-json-view applies inline colour styles derived from the active theme.
// In jsdom the `style={{ background: "transparent" }}` override causes the
// outer div's backgroundColor to be "transparent" for both themes, so we
// cannot use it to distinguish them.
//
// Instead we check a brace/bracket colour that is unique to each theme:
//   - rjv-default:  brace colour = rgb(0, 43, 54)  (dark blue-green)
//   - monokai:      brace colour = rgb(249, 248, 245)  (near-white)

describe("JsonTreeView", () => {
  afterEach(() => {
    document.documentElement.classList.remove("light", "dark");
  });

  it("uses monokai brace colour when <html> is dark", async () => {
    document.documentElement.classList.add("dark");
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(<JsonTreeView value={{ a: 1 }} />));
    });
    // The opening brace span has a fontWeight:bold inline style with theme colour.
    const brace = container.querySelector(
      "[class*=react-json-view] span[style*='font-weight: bold']",
    ) as HTMLElement;
    expect(brace).toBeTruthy();
    // monokai uses near-white for braces; rjv-default uses dark blue-green.
    const colour = (brace.style.color || "").toLowerCase();
    expect(colour).toBe("rgb(249, 248, 245)");
  });

  it("uses rjv-default brace colour when <html> is light", async () => {
    document.documentElement.classList.add("light");
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(<JsonTreeView value={{ a: 1 }} />));
    });
    const brace = container.querySelector(
      "[class*=react-json-view] span[style*='font-weight: bold']",
    ) as HTMLElement;
    expect(brace).toBeTruthy();
    const colour = (brace.style.color || "").toLowerCase();
    expect(colour).toBe("rgb(0, 43, 54)");
  });
});
