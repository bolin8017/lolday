import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

type SchemaNode = { [key: string]: unknown };

/**
 * Normalize a JSON Schema so that properties with sibling keywords alongside
 * `$ref` are wrapped in `allOf`.  RJSF v5 / ajv8 processes `allOf` correctly;
 * bare `$ref`+sibling patterns (valid in JSON Schema draft 2019-09+) can cause
 * a TypeError in RJSF's production bundle.
 *
 * Transforms:
 *   { "$ref": "#/$defs/X", "default": {...} }
 * into:
 *   { "allOf": [{ "$ref": "#/$defs/X" }], "default": {...} }
 */
function normalizeSchema(node: unknown): unknown {
  if (node === null || typeof node !== "object") return node;
  if (Array.isArray(node)) return node.map(normalizeSchema);

  const obj = node as SchemaNode;
  const out: SchemaNode = {};

  for (const [k, v] of Object.entries(obj)) {
    out[k] = typeof v === "object" && v !== null ? normalizeSchema(v) : v;
  }

  // If this object has both "$ref" and other meaningful sibling keys, wrap $ref in allOf
  if ("$ref" in out) {
    const { $ref, ...rest } = out as { $ref: string } & SchemaNode;
    const hasSiblings = Object.keys(rest).some(
      (k) => !["title", "description"].includes(k),
    );
    if (hasSiblings) {
      return { allOf: [{ $ref }], ...rest };
    }
  }

  return out;
}

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  const normalizedSchema = normalizeSchema(schema) as object;
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
