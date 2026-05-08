import { describe, it, expect } from "vitest";
import {
  deriveUiSchemaFromSchema,
  fillDefaults,
} from "@/components/forms/RjsfConfigForm.logic";

describe("deriveUiSchemaFromSchema", () => {
  it("maps bounded float (min+max) to rangeSlider", () => {
    const schema = {
      type: "object",
      properties: { t: { type: "number", minimum: 0, maximum: 1 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect((ui.t as Record<string, unknown>)["ui:widget"]).toBe("rangeSlider");
  });

  it("maps integer to stepper", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", minimum: 1 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect((ui.n as Record<string, unknown>)["ui:widget"]).toBe("stepper");
  });

  it("maps unbounded float to numericInput", () => {
    const schema = {
      type: "object",
      properties: { lr: { type: "number", exclusiveMinimum: 0 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect((ui.lr as Record<string, unknown>)["ui:widget"]).toBe(
      "numericInput",
    );
  });

  it("maps boolean to switch", () => {
    const schema = {
      type: "object",
      properties: { flag: { type: "boolean" } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect((ui.flag as Record<string, unknown>)["ui:widget"]).toBe("switch");
  });

  it("does not set ui:widget for string with enum", () => {
    const schema = {
      type: "object",
      properties: { mode: { type: "string", enum: ["a", "b"] } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(
      (ui.mode as Record<string, unknown> | undefined)?.["ui:widget"],
    ).toBeUndefined();
  });
});

describe("fillDefaults", () => {
  it("fills default for missing key", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    expect(fillDefaults(schema as any, {})).toEqual({ n: 100 });
  });

  it("does not overwrite existing value", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    expect(fillDefaults(schema as any, { n: 200 })).toEqual({ n: 200 });
  });

  it("does not fill when no default", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer" } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    expect(fillDefaults(schema as any, {})).toEqual({});
  });

  it("respects null default for nullable union", () => {
    const schema = {
      type: "object",
      properties: {
        max_depth: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          default: null,
        },
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    expect(fillDefaults(schema as any, {})).toEqual({ max_depth: null });
  });
});
