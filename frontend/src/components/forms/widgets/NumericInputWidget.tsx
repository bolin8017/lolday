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
  // Sync from external prop changes (e.g. form reset).
  // Compare the *parsed* draft against the incoming value so we don't clobber
  // a partial decimal entry during keyboard edits (e.g. "0." parses as 0,
  // same as the round-tripped propValue=0 — without the guard the effect
  // would reset "0." → "0" before the user finishes typing).
  useEffect(() => {
    if (value === undefined || value === null) {
      if (draft !== "") setDraft("");
    } else {
      const parsed = Number(draft);
      const propNumeric = Number(value);
      if (!Number.isNaN(propNumeric) && parsed !== propNumeric) {
        setDraft(String(value));
      }
    }
    // intentionally don't depend on draft to prevent loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
