import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CollapsibleCard } from "@/components/common/CollapsibleCard";

/**
 * ``CollapsibleCard`` is a small reusable card with a clickable header
 * that toggles the body open/closed. The pattern is used wherever a
 * page surfaces optional / advanced detail. The component owns its own
 * open state (no external prop), so behavioural tests cover:
 *
 * - Default closed: body hidden, right-chevron icon.
 * - ``defaultOpen=true``: body visible, down-chevron icon.
 * - Click header → toggles state and swaps the chevron.
 * - Title text appears in the header.
 * - Body content (passed as children) is only present when open.
 */

describe("CollapsibleCard", () => {
  it("renders the title text in the header", () => {
    render(
      <CollapsibleCard title="Advanced">
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });

  it("hides the body by default", () => {
    render(
      <CollapsibleCard title="Advanced">
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    expect(screen.queryByTestId("body")).not.toBeInTheDocument();
  });

  it("shows the body when defaultOpen is true", () => {
    render(
      <CollapsibleCard title="Advanced" defaultOpen>
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    expect(screen.getByTestId("body")).toBeInTheDocument();
  });

  it("toggles open and closed when the header is clicked", async () => {
    const user = userEvent.setup();
    render(
      <CollapsibleCard title="Advanced">
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    // Click the title text — its parent <h3> (CardTitle) carries the
    // onClick via the CardHeader wrapper, so clicking the inner Text
    // bubbles up to the header handler.
    const header = screen.getByText("Advanced").closest("div")!;
    await user.click(header);
    expect(screen.getByTestId("body")).toBeInTheDocument();
    await user.click(header);
    expect(screen.queryByTestId("body")).not.toBeInTheDocument();
  });

  it("uses ChevronRight when closed", () => {
    render(
      <CollapsibleCard title="Advanced">
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    // lucide-react ships SVG icons with `lucide-chevron-right` /
    // `lucide-chevron-down` as part of their generated class names.
    const title = screen.getByText("Advanced").parentElement!;
    expect(title.querySelector(".lucide-chevron-right")).not.toBeNull();
    expect(title.querySelector(".lucide-chevron-down")).toBeNull();
  });

  it("uses ChevronDown when open (defaultOpen)", () => {
    // Use a fresh render — useState only honours the initial defaultOpen,
    // so a rerender with a different defaultOpen would not flip the state.
    render(
      <CollapsibleCard title="Advanced" defaultOpen>
        <div data-testid="body">body content</div>
      </CollapsibleCard>,
    );
    const title = screen.getByText("Advanced").parentElement!;
    expect(title.querySelector(".lucide-chevron-down")).not.toBeNull();
    expect(title.querySelector(".lucide-chevron-right")).toBeNull();
  });
});
