import type { RJSFSchema, StrictRJSFSchema, UiSchema } from "@rjsf/utils";

export function deriveUiSchemaFromSchema(schema: RJSFSchema): UiSchema {
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  walk(schema, ui);
  return ui;
}

function walk(node: StrictRJSFSchema, ui: UiSchema): void {
  const { properties } = node;
  if (!properties) return;
  // Object.entries doesn't narrow JSONSchema7Definition to its union member;
  // cast to the known union before the boolean guard below.
  const entries = Object.entries(properties) as [
    string,
    StrictRJSFSchema | boolean,
  ][];
  for (const [k, child] of entries) {
    if (typeof child === "boolean") continue;
    const childUi: UiSchema = (ui[k] as UiSchema) ?? {};
    if (typeof child.description === "string") {
      childUi["ui:help"] = child.description;
    }
    if (child.default !== undefined) {
      childUi["ui:placeholder"] = `Default: ${JSON.stringify(child.default)}`;
    }
    if (Object.keys(childUi).length > 0) ui[k] = childUi;
    walk(child, childUi);
  }
}

export function fillDefaults(
  schema: RJSFSchema,
  current: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...current };
  const { properties } = schema as StrictRJSFSchema;
  if (!properties) return out;
  const entries = Object.entries(properties) as [
    string,
    StrictRJSFSchema | boolean,
  ][];
  for (const [k, child] of entries) {
    if (typeof child === "boolean") continue;
    if (out[k] !== undefined) continue;
    if (child.default !== undefined) {
      out[k] = child.default;
    }
  }
  return out;
}
