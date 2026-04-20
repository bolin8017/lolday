import Form from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { useMemo } from "react";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

// Sibling keywords RJSF tolerates next to `$ref` without the allOf wrap.
// Keep this set minimal — adding annotation keywords like `default` would
// defeat the workaround, since `default` is the trigger we're working around.
const NON_WRAPPING_SIBLINGS = new Set(["title", "description"]);

/**
 * Wrap `$ref` in `allOf` when sibling keywords are present.  Bare `$ref`+sibling
 * patterns (valid in JSON Schema 2019-09+) crash RJSF v5's production bundle:
 *   { "$ref": "#/$defs/X", "default": {...} }
 *     → { "allOf": [{ "$ref": "#/$defs/X" }], "default": {...} }
 * Idempotent — re-running on already-wrapped schemas is a no-op.
 */
function normalizeSchema(node: unknown): unknown {
  if (node === null || typeof node !== "object") return node;
  if (Array.isArray(node)) return node.map(normalizeSchema);

  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node)) {
    out[k] = normalizeSchema(v);
  }

  if (typeof out.$ref === "string") {
    const { $ref, ...rest } = out;
    const hasSiblings = Object.keys(rest).some((k) => !NON_WRAPPING_SIBLINGS.has(k));
    if (hasSiblings) {
      return { allOf: [{ $ref }], ...rest };
    }
  }

  return out;
}

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  const normalizedSchema = useMemo(() => normalizeSchema(schema) as RJSFSchema, [schema]);
  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={normalizedSchema}
        validator={validator}
        formData={value}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
        uiSchema={{ "ui:submitButtonOptions": { norender: true } }}
      >
        {/* No submit — parent form owns submission */}
        <span />
      </Form>
    </div>
  );
}
