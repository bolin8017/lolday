import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LogTail } from "@/components/common/LogTail";

describe("LogTail", () => {
  it("renders the text content verbatim (preserves multiline formatting)", () => {
    const { container } = render(<LogTail text={"line1\nline2\nline3"} />);
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toBe("line1\nline2\nline3");
  });

  it("renders '(no output)' placeholder when text is empty", () => {
    const { container } = render(<LogTail text="" />);
    expect(container.querySelector("pre")?.textContent).toBe("(no output)");
  });

  it("renders inside a <pre> so monospace + whitespace are preserved", () => {
    const { container } = render(<LogTail text="  spaces  preserved" />);
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre?.className).toMatch(/font-mono/);
  });

  it("scrolls to the bottom on text change (auto-tail behaviour)", () => {
    // jsdom's scrollHeight is 0 by default and scrollTop assignments succeed.
    // Spy on the scrollTop setter to assert it gets touched after a text
    // update — that's the contract we care about (consumers polling for new
    // log lines should always see the tail, not the head).
    const { container, rerender } = render(<LogTail text="line1" />);
    const pre = container.querySelector("pre") as HTMLPreElement;
    // Set scrollTop via Object.defineProperty so we can observe writes.
    let writes: number[] = [];
    Object.defineProperty(pre, "scrollTop", {
      configurable: true,
      get() {
        return 0;
      },
      set(v: number) {
        writes.push(v);
      },
    });
    Object.defineProperty(pre, "scrollHeight", {
      configurable: true,
      get() {
        return 9999;
      },
    });
    // Reset writes captured during initial mount; the contract is "scrolls
    // on text change" — re-trigger the effect by changing the text.
    writes = [];
    rerender(<LogTail text="line1\nline2" />);
    expect(writes).toContain(9999);
  });

  it("applies a className override on top of the default styling", () => {
    const { container } = render(<LogTail text="x" className="custom-class" />);
    expect(container.querySelector("pre")?.className).toMatch(/custom-class/);
  });
});
