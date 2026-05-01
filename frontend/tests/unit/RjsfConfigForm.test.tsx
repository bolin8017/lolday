import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { RjsfConfigForm } from "@/components/forms/RjsfConfigForm";

describe("RjsfConfigForm", () => {
  it("renders schema description exactly once (no help-block duplication)", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "Number of trees", default: 100 },
      },
    };
    render(<RjsfConfigForm schema={schema} value={{}} onChange={() => {}} />);
    // Without ui:help mirroring, RJSF renders description only as
    // <p class="field-description">. Asserting exactly one instance guards
    // against a future regression that re-introduces the duplication.
    expect(screen.getAllByText(/Number of trees/i)).toHaveLength(1);
  });

  it("pre-populates defaults via onChange on mount", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    const onChange = vi.fn();
    render(<RjsfConfigForm schema={schema} value={{}} onChange={onChange} />);
    expect(onChange).toHaveBeenCalledWith({ n: 100 });
  });

  it("Reset to defaults button restores defaults", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    const onChange = vi.fn();
    render(
      <RjsfConfigForm schema={schema} value={{ n: 200 }} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));
    expect(onChange).toHaveBeenLastCalledWith({ n: 100 });
  });
});
