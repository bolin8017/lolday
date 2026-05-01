import { describe, it, expect } from "vitest";
import {
  deriveUiSchemaFromSchema,
  fillDefaults,
} from "@/components/forms/RjsfConfigForm.logic";

describe("deriveUiSchemaFromSchema", () => {
  it("does not mirror description into ui:help (RJSF renders description natively)", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "Number of trees." },
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    expect(deriveUiSchemaFromSchema(schema as any)).toEqual({
      "ui:submitButtonOptions": { norender: true },
    });
  });

  it("ui:placeholder from default", () => {
    const schema = {
      type: "object",
      properties: { lr: { type: "number", default: 0.001 } },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(ui.lr["ui:placeholder"]).toBe("Default: 0.001");
  });

  it("description plus default → only ui:placeholder (description is not duplicated)", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "trees", default: 100 },
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(ui.n).toEqual({
      "ui:placeholder": "Default: 100",
    });
  });

  it("recurses into nested object properties for ui:placeholder", () => {
    const schema = {
      type: "object",
      properties: {
        optimizer: {
          type: "object",
          properties: {
            lr: { type: "number", description: "Learning rate", default: 0.01 },
          },
        },
      },
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- test literal is a partial schema subset
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(ui.optimizer).toEqual({
      lr: {
        "ui:placeholder": "Default: 0.01",
      },
    });
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
