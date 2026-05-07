import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OwnerLabel } from "@/components/users/OwnerLabel";

describe("OwnerLabel", () => {
  it("renders the handle text", () => {
    render(<OwnerLabel handle="alice" />);
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("renders the user icon", () => {
    render(<OwnerLabel handle="alice" />);
    expect(screen.getByLabelText("user")).toBeInTheDocument();
  });
});
