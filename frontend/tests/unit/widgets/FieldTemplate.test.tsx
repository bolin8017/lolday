import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { FieldTemplate } from "@/components/forms/templates/FieldTemplate";

const baseProps = {
  id: "f",
  classNames: "",
  label: "field_x",
  required: false,
  disabled: false,
  readonly: false,
  errors: <></>,
  help: <></>,
  description: <p className="text-muted-foreground">desc</p>,
  rawDescription: "desc",
  rawHelp: "",
  rawErrors: [] as string[],
  schema: { type: "number", default: 0.5 } as const,
  uiSchema: {},
  formContext: {},
  registry: {} as never,
  hidden: false,
  displayLabel: true,
  onChange: () => {},
  onKeyChange: () => () => {},
  onDropPropertyClick: () => () => {},
};

describe("FieldTemplate", () => {
  it("shows 'default 0.5' badge when value === default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.5}>
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText(/default 0\.5/i)).toBeInTheDocument();
    expect(screen.queryByText(/modified/i)).toBeNull();
  });

  it("shows 'modified' badge when value !== default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.7}>
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText(/modified/i)).toBeInTheDocument();
  });

  it("renders a reset button when value !== default and onResetField is provided", () => {
    render(
      <FieldTemplate
        {...baseProps}
        formData={0.7}
        formContext={{ onResetField: () => {} }}
      >
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByRole("button", { name: /reset/i })).toBeInTheDocument();
  });

  it("does not render reset button when value === default", () => {
    render(
      <FieldTemplate
        {...baseProps}
        formData={0.5}
        formContext={{ onResetField: () => {} }}
      >
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.queryByRole("button", { name: /reset/i })).toBeNull();
  });

  it("calls formContext.onResetField with the field id when reset is clicked", async () => {
    const onResetField = vi.fn();
    render(
      <FieldTemplate
        {...baseProps}
        formData={0.7}
        formContext={{ onResetField }}
      >
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    await userEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(onResetField).toHaveBeenCalledWith("f");
  });

  it("renders the description below the control", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.5}>
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText("desc")).toBeInTheDocument();
  });
});
