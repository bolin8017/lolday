import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VisibilityBadge } from "@/components/models/VisibilityBadge";

describe("VisibilityBadge", () => {
  it("renders 'Public' label with globe icon for visibility=public", () => {
    render(<VisibilityBadge visibility="public" />);
    expect(screen.getByText("Public")).toBeInTheDocument();
    expect(screen.getByLabelText(/globe/i)).toBeInTheDocument();
  });

  it("renders 'Private' label with lock icon for visibility=private", () => {
    render(<VisibilityBadge visibility="private" />);
    expect(screen.getByText("Private")).toBeInTheDocument();
    expect(screen.getByLabelText(/lock/i)).toBeInTheDocument();
  });

  it("hides label when iconOnly=true", () => {
    render(<VisibilityBadge visibility="public" iconOnly />);
    expect(screen.queryByText("Public")).not.toBeInTheDocument();
    expect(screen.getByLabelText(/globe/i)).toBeInTheDocument();
  });
});
