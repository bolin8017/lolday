import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
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
    fireEvent.click(
      screen.getByRole("button", { name: /reset all to defaults/i }),
    );
    expect(onChange).toHaveBeenLastCalledWith({ n: 100 });
  });

  it("renders typed widgets for each field type", () => {
    const schema = {
      type: "object",
      properties: {
        threshold: { type: "number", minimum: 0, maximum: 1, default: 0.5 },
        n_estimators: { type: "integer", minimum: 1, default: 100 },
        flag: { type: "boolean", default: false },
      },
    };
    render(<RjsfConfigForm schema={schema} value={{}} onChange={() => {}} />);
    // bounded float → slider role
    expect(screen.getByRole("slider")).toBeInTheDocument();
    // integer → ± buttons (and a spinbutton input)
    expect(
      screen.getByRole("button", { name: /increment/i }),
    ).toBeInTheDocument();
    // boolean → switch role
    expect(screen.getByRole("switch")).toBeInTheDocument();
  });

  it("shows 'default X' badge per field initially", () => {
    const schema = {
      type: "object",
      properties: {
        threshold: { type: "number", minimum: 0, maximum: 1, default: 0.5 },
      },
    };
    render(
      <RjsfConfigForm
        schema={schema}
        value={{ threshold: 0.5 }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/default 0\.5/i)).toBeInTheDocument();
  });

  describe("per-field reset (formContext.onResetField)", () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
      warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    });
    afterEach(() => {
      warnSpy.mockRestore();
    });

    it("restores a top-level field to its schema default", () => {
      // Field is modified (value 0.9 vs default 0.5) so FieldTemplate
      // surfaces the per-field reset button. Click must call onChange
      // with the field reset to its schema default while leaving siblings
      // untouched.
      const schema = {
        type: "object",
        properties: {
          threshold: { type: "number", minimum: 0, maximum: 1, default: 0.5 },
          other: { type: "integer", default: 7 },
        },
      };
      const onChange = vi.fn();
      render(
        <RjsfConfigForm
          schema={schema}
          value={{ threshold: 0.9, other: 7 }}
          onChange={onChange}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /^reset$/i }));
      expect(onChange).toHaveBeenLastCalledWith({
        threshold: 0.5,
        other: 7,
      });
    });

    it("warns and no-ops on a nested field whose id has no top-level default", () => {
      // fillDefaults is shallow (top-level properties only), so for a
      // nested schema RJSF generates id `root_optimizer_lr` which strips
      // to key `optimizer_lr` — absent from defaults. The handler must
      // refuse to write a junk entry.
      const schema = {
        type: "object",
        properties: {
          optimizer: {
            type: "object",
            properties: {
              lr: { type: "number", default: 0.01 },
            },
          },
        },
      };
      const onChange = vi.fn();
      render(
        <RjsfConfigForm
          schema={schema}
          value={{ optimizer: { lr: 0.5 } }}
          onChange={onChange}
        />,
      );
      // Initial fillDefaults onChange fires on mount with {} (no top-level
      // default for `optimizer`). Track only post-mount calls.
      onChange.mockClear();
      fireEvent.click(screen.getByRole("button", { name: /^reset$/i }));
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining("optimizer_lr"),
      );
      // No state write — the no-op path must not call onChange.
      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
