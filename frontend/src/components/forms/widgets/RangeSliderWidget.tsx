import { type ChangeEvent, useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import type { WidgetProps } from "@rjsf/utils";

const STEP_DEFAULT = 0.01;

export function RangeSliderWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : 0;
  const max = typeof schema.maximum === "number" ? schema.maximum : 1;
  const step =
    typeof schema.multipleOf === "number" ? schema.multipleOf : STEP_DEFAULT;

  const propNumeric = typeof value === "number" ? value : Number(value ?? min);

  // Local draft: keeps the raw string the user is typing so we don't fight
  // against a controlled-component re-render before the user finishes typing.
  const [draft, setDraft] = useState<string>(
    Number.isNaN(propNumeric) ? "" : String(propNumeric),
  );

  // Sync from external prop changes (e.g. slider moving, form reset).
  // Compare the *parsed* draft against propNumeric so we don't clobber a
  // partial decimal entry (e.g. "0." parses as 0, same as propNumeric=0 after
  // a round-trip through onChange — without the guard the effect would reset
  // "0." → "0" before the user finishes typing).
  // Also skip when draft is empty or trailing-decimal (incomplete entry).
  useEffect(() => {
    if (Number.isNaN(propNumeric)) return;
    const parsed = Number(draft);
    // draft is empty or partial (e.g. "0.", "-") — user is mid-entry, don't override
    if (draft === "" || Number.isNaN(parsed)) return;
    if (parsed !== propNumeric) {
      setDraft(String(propNumeric));
    }
    // intentionally don't depend on draft to prevent loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propNumeric]);

  const sliderValue = Number.isNaN(propNumeric) ? min : propNumeric;

  function handleNumeric(e: ChangeEvent<HTMLInputElement>) {
    const raw = e.target.value;
    setDraft(raw);
    const v = raw === "" ? null : Number(raw);
    onChange(v === null || Number.isNaN(v) ? undefined : v);
  }

  function handleSlider([next]: number[]) {
    onChange(next);
  }

  return (
    <div className="flex items-center gap-3">
      <Slider
        value={[sliderValue]}
        min={min}
        max={max}
        step={step}
        onValueChange={handleSlider}
        disabled={disabled || readonly}
        className="flex-1"
      />
      <Input
        id={id}
        type="number"
        value={draft}
        onChange={handleNumeric}
        min={min}
        max={max}
        step={step}
        disabled={disabled || readonly}
        className="w-20 font-mono text-sm"
      />
    </div>
  );
}
