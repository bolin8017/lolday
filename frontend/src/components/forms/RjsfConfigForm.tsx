import Form from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { useCallback, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { deriveUiSchemaFromSchema, fillDefaults } from "./RjsfConfigForm.logic";
import { FieldTemplate } from "./templates/FieldTemplate";
import { RangeSliderWidget } from "./widgets/RangeSliderWidget";
import { StepperWidget } from "./widgets/StepperWidget";
import { NumericInputWidget } from "./widgets/NumericInputWidget";
import { SwitchWidget } from "./widgets/SwitchWidget";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

const NON_WRAPPING_SIBLINGS = new Set(["title", "description"]);

function normalizeSchema(node: unknown): unknown {
  if (node === null || typeof node !== "object") return node;
  if (Array.isArray(node)) return node.map(normalizeSchema);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node)) out[k] = normalizeSchema(v);
  if (typeof out.$ref === "string") {
    const { $ref, ...rest } = out;
    const hasSiblings = Object.keys(rest).some(
      (k) => !NON_WRAPPING_SIBLINGS.has(k),
    );
    if (hasSiblings) return { allOf: [{ $ref }], ...rest };
  }
  return out;
}

const widgets = {
  rangeSlider: RangeSliderWidget,
  stepper: StepperWidget,
  numericInput: NumericInputWidget,
  switch: SwitchWidget,
};

const templates = { FieldTemplate };

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  const normalizedSchema = useMemo(
    () => normalizeSchema(schema) as RJSFSchema,
    [schema],
  );
  const uiSchema = useMemo(
    () => deriveUiSchemaFromSchema(normalizedSchema),
    [normalizedSchema],
  );
  const defaults = useMemo(
    () => fillDefaults(normalizedSchema, {}),
    [normalizedSchema],
  );

  useEffect(() => {
    onChange(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to schema changes
  }, [normalizedSchema]);

  const onResetField = useCallback(
    (fieldId: string) => {
      // RJSF builds field ids as `root_<key>` (configurable via idPrefix).
      const key = fieldId.replace(/^root_/, "");
      const next = {
        ...value,
        [key]: (defaults as Record<string, unknown>)[key],
      };
      onChange(next);
    },
    [value, defaults, onChange],
  );

  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={normalizedSchema}
        uiSchema={uiSchema}
        validator={validator}
        formData={value}
        widgets={widgets}
        templates={templates}
        formContext={{ onResetField }}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
      >
        <div className="mt-4 flex justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onChange(defaults)}
          >
            Reset all to defaults
          </Button>
        </div>
      </Form>
    </div>
  );
}
