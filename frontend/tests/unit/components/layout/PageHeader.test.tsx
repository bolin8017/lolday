import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "@/components/layout/PageHeader";

describe("PageHeader", () => {
  it("renders the title", () => {
    const { getByRole } = render(<PageHeader title="Jobs" />);
    expect(getByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  });

  it("renders actions in the actions slot", () => {
    const { getByText } = render(
      <PageHeader title="Jobs" actions={<button>Submit</button>} />,
    );
    expect(getByText("Submit")).toBeInTheDocument();
  });

  it("renders a description below the title row", () => {
    const { getByText } = render(
      <PageHeader title="Users" description="Manage roles" />,
    );
    expect(getByText("Manage roles")).toBeInTheDocument();
  });

  it("places title and actions inside a flex container", () => {
    const { getByRole, getByText } = render(
      <PageHeader title="Jobs" actions={<button>Submit</button>} />,
    );
    const heading = getByRole("heading");
    const button = getByText("Submit");
    // Both heading and button must share an ancestor with `flex` class
    expect(heading.parentElement?.className).toMatch(/flex/);
    // Button is wrapped in actions div which is sibling of heading
    expect(button.parentElement?.parentElement).toBe(heading.parentElement);
  });
});
