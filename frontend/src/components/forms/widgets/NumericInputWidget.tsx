import { type ChangeEvent, useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import type { WidgetProps } from "@rjsf/utils";

export function NumericInputWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : undefined;
  const max = typeof schema.maximum === "number" ? schema.maximum : undefined;
  const step =
    typeof schema.multipleOf === "number" ? schema.multipleOf : "any";

  // Draft-state pattern (same rationale as RangeSliderWidget): controlled
  // numeric inputs in RJSF can clobber typing if we bind the prop directly.
  const [draft, setDraft] = useState<string>(
    value === undefined || value === null ? "" : String(value),
  );
  useEffect(() => {
    setDraft(value === undefined || value === null ? "" : String(value));
  }, [value]);

  function handle(e: ChangeEvent<HTMLInputElement>) {
    const next = e.target.value;
    setDraft(next);
    if (next === "") {
      onChange(undefined);
      return;
    }
    const num = Number(next);
    if (!Number.isNaN(num)) onChange(num);
  }

  return (
    <Input
      id={id}
      type="number"
      value={draft}
      onChange={handle}
      min={min}
      max={max}
      step={step}
      disabled={disabled || readonly}
      className="font-mono text-sm"
    />
  );
}
