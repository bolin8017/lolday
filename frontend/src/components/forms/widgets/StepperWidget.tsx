import { type ChangeEvent, useEffect, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { WidgetProps } from "@rjsf/utils";

export function StepperWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : -Infinity;
  const max = typeof schema.maximum === "number" ? schema.maximum : Infinity;
  const step = typeof schema.multipleOf === "number" ? schema.multipleOf : 1;

  const propNumeric = typeof value === "number" ? value : Number(value ?? min);

  // Local draft: keeps the raw string the user is typing so we don't fight
  // against a controlled-component re-render before the user finishes typing.
  const [draft, setDraft] = useState<string>(
    Number.isNaN(propNumeric) ? "" : String(propNumeric),
  );

  // Sync from external prop changes (e.g. button bump, form reset).
  // Compare the *parsed* draft against propNumeric so we don't clobber a
  // partial decimal entry during keyboard edits.
  // Also skip when draft is empty or NaN (user is mid-edit / just cleared).
  useEffect(() => {
    if (draft === "") return; // user is mid-edit (cleared)
    const parsed = Number(draft);
    if (Number.isNaN(parsed)) return;
    if (!Number.isNaN(propNumeric) && parsed !== propNumeric) {
      setDraft(String(propNumeric));
    }
    // intentionally don't depend on draft to prevent loop -- clobber-on-empty bug
    // eslint-disable-next-line react-hooks/exhaustive-deps -- avoid loop on draft changes
  }, [propNumeric]);

  const numeric = Number.isNaN(propNumeric)
    ? isFinite(min)
      ? min
      : 0
    : propNumeric;

  function bump(delta: number) {
    const next = numeric + delta;
    if (next < min || next > max) return;
    onChange(next);
  }

  function handleInput(e: ChangeEvent<HTMLInputElement>) {
    const raw = e.target.value;
    setDraft(raw);
    const v = raw === "" ? null : Number(raw);
    onChange(v === null || Number.isNaN(v) ? undefined : v);
  }

  const cantDec = numeric - step < min;
  const cantInc = numeric + step > max;

  return (
    <div className="inline-flex items-center rounded-md border bg-background">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => bump(-step)}
        disabled={disabled || readonly || cantDec}
        aria-label="decrement"
        className="h-8 w-8 rounded-r-none p-0"
      >
        <Minus className="h-4 w-4" />
      </Button>
      <Input
        id={id}
        type="number"
        value={draft}
        onChange={handleInput}
        min={isFinite(min) ? min : undefined}
        max={isFinite(max) ? max : undefined}
        step={step}
        disabled={disabled || readonly}
        className="h-8 w-20 rounded-none border-x text-center font-mono text-sm"
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => bump(step)}
        disabled={disabled || readonly || cantInc}
        aria-label="increment"
        className="h-8 w-8 rounded-l-none p-0"
      >
        <Plus className="h-4 w-4" />
      </Button>
    </div>
  );
}
