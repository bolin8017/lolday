import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StickyFormFooter } from "@/components/forms/StickyFormFooter";

/**
 * StickyFormFooter is a presentational wrapper used by every form CTA bar
 * (`RegisterDetectorForm`, `DatasetUploadForm`, `DiscordIdForm`, …). The
 * three things consumers rely on:
 *
 * 1. Children render inside the bar (otherwise the Submit/Cancel buttons
 *    disappear on mobile).
 * 2. The mobile-margin compensation (`-mx-4 sm:-mx-6`) is present so the
 *    bar visually spans edge-to-edge inside the parent's `p-4 sm:p-6`
 *    padding cadence.
 * 3. `sticky bottom-0` + safe-area inset stay in place — these are the
 *    invariants the component's docstring calls out as the iPhone-notch /
 *    sticky-CTA contract.
 * 4. `className` overrides merge in via `cn` (consumers occasionally add
 *    `hidden` to suppress the bar on a specific step).
 */
describe("StickyFormFooter", () => {
  it("renders children inside the sticky bar", () => {
    render(
      <StickyFormFooter>
        <button type="button">submit</button>
        <button type="button">cancel</button>
      </StickyFormFooter>,
    );
    expect(screen.getByRole("button", { name: "submit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "cancel" })).toBeInTheDocument();
  });

  it("applies the sticky-bottom + safe-area-inset utility classes", () => {
    const { container } = render(
      <StickyFormFooter>
        <span>inner</span>
      </StickyFormFooter>,
    );
    const bar = container.firstElementChild as HTMLElement;
    expect(bar).not.toBeNull();
    // sticky + flex are the structural contract.
    expect(bar.className).toMatch(/\bsticky\b/);
    expect(bar.className).toMatch(/\bbottom-0\b/);
    expect(bar.className).toMatch(/\bflex\b/);
    // Negative mobile margin cancels the parent's `p-4 sm:p-6` padding so
    // the bar spans edge-to-edge — the docstring calls this out as the
    // contract that must match the parent shell's padding cadence.
    expect(bar.className).toMatch(/-mx-4/);
    expect(bar.className).toMatch(/sm:-mx-6/);
    // Safe-area inset clears the iOS home indicator.
    expect(bar.className).toContain(
      "pb-[calc(0.75rem+env(safe-area-inset-bottom))]",
    );
  });

  it("merges a caller-supplied className into the bar", () => {
    const { container } = render(
      <StickyFormFooter className="custom-cls">
        <span>x</span>
      </StickyFormFooter>,
    );
    const bar = container.firstElementChild as HTMLElement;
    expect(bar.className).toContain("custom-cls");
    // Base classes survive the merge — `cn` (tailwind-merge) must not
    // strip the structural utilities when the caller adds their own.
    expect(bar.className).toMatch(/\bsticky\b/);
  });
});
