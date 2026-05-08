import type { RJSFSchema, StrictRJSFSchema, UiSchema } from "@rjsf/utils";

export function deriveUiSchemaFromSchema(schema: RJSFSchema): UiSchema {
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  walk(schema, ui);
  return ui;
}

function hasType(node: StrictRJSFSchema, t: string): boolean {
  if (node.type === t) return true;
  // Array.includes requires the exact element type; cast via (string[]) for
  // the nullable-array case (e.g. ["number", "null"] from Pydantic Optional).
  if (Array.isArray(node.type) && (node.type as string[]).includes(t))
    return true;
  return false;
}

function walk(node: StrictRJSFSchema, ui: UiSchema): void {
  const { properties } = node;
  if (!properties) return;
  const entries = Object.entries(properties) as [
    string,
    StrictRJSFSchema | boolean,
  ][];
  for (const [k, child] of entries) {
    if (typeof child === "boolean") continue;
    const childUi: UiSchema = (ui[k] as UiSchema) ?? {};

    // Type → widget mapping. Selected widget names are registered in
    // RjsfConfigForm.tsx's `widgets` prop.
    // hasType handles both string types ("number") and nullable arrays
    // (["number", "null"]) — Pydantic Optional[float] exports the latter.
    const isNumber = hasType(child, "number");
    const isInteger = hasType(child, "integer");
    const isBoolean = hasType(child, "boolean");
    const hasMin = typeof child.minimum === "number";
    const hasMax = typeof child.maximum === "number";

    if (isNumber && hasMin && hasMax) {
      childUi["ui:widget"] = "rangeSlider";
    } else if (isInteger) {
      childUi["ui:widget"] = "stepper";
    } else if (isNumber) {
      childUi["ui:widget"] = "numericInput";
    } else if (isBoolean) {
      childUi["ui:widget"] = "switch";
    }
    // string + enum → default SelectWidget (RJSF picks it automatically)

    walk(child, childUi);
    if (Object.keys(childUi).length > 0) ui[k] = childUi;
  }
}

// NOTE: iterates only top-level properties — same flat-schema assumption as
// onResetField in RjsfConfigForm.tsx; revisit if a future detector introduces
// nested config keys.
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
