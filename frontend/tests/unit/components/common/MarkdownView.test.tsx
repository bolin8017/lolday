import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownView } from "@/components/common/MarkdownView";

describe("MarkdownView", () => {
  it("renders headings", () => {
    render(<MarkdownView source="## Heading" />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "Heading",
    );
  });

  it("renders code blocks", () => {
    render(<MarkdownView source={"```\ncode\n```"} />);
    const code = screen.getByText("code");
    // react-markdown renders code blocks as <code> inside <pre>
    expect(code.tagName).toBe("CODE");
  });

  it("renders unordered lists", () => {
    render(<MarkdownView source={"- a\n- b"} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("does not execute raw HTML <script> tags", () => {
    const dangerous = "<script>window.__pwned__=true</script>plain";
    render(<MarkdownView source={dangerous} />);
    // react-markdown by default does not interpret raw HTML
    // (without `rehype-raw`); raw HTML is rendered as plain text or stripped
    // depending on config — either way, no script execution
    expect(
      (window as unknown as { __pwned__?: boolean }).__pwned__,
    ).toBeUndefined();
    expect(screen.getByText(/plain/)).toBeInTheDocument();
  });

  it("renders empty source as nothing visible", () => {
    const { container } = render(<MarkdownView source="" />);
    expect(container.textContent).toBe("");
  });

  it("renders inline code", () => {
    render(<MarkdownView source={"use `foo()` here"} />);
    expect(screen.getByText("foo()").tagName).toBe("CODE");
  });
});
