import { RotateCcw } from "lucide-react";
import type { FieldTemplateProps } from "@rjsf/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import type { RjsfFormContext } from "../RjsfConfigForm.types";

/**
 * RJSF field template — renders the label row (with default / modified badge
 * and per-field reset button) above the widget, then the description below.
 *
 * Per-field reset is opt-in: shows only when (a) the current value differs
 * from schema.default AND (b) `formContext.onResetField` is provided. The
 * parent form (RjsfConfigForm) wires `onResetField` via formContext using
 * the shared RjsfFormContext interface — see RjsfConfigForm.types.ts.
 *
 * Default-vs-modified detection uses reference equality (formData !== default).
 * This is correct for scalar defaults (number, integer, boolean, string) and
 * the only kind currently produced by elfrfdet/elfcnndet schemas. Object/array
 * defaults would always read as "modified" because of reference inequality —
 * revisit the comparison if a future detector ships compound defaults.
 */
export function FieldTemplate(props: FieldTemplateProps) {
  const {
    id,
    classNames,
    label,
    required,
    children,
    description,
    schema,
    formData,
    displayLabel,
    formContext,
  } = props;

  const ctx = (formContext ?? {}) as RjsfFormContext;
  const defaultValue = (schema as { default?: unknown }).default;
  const isModified = defaultValue !== undefined && formData !== defaultValue;
  const showReset = isModified && typeof ctx.onResetField === "function";

  return (
    <div className={`mb-4 ${classNames ?? ""}`}>
      {displayLabel && (
        <div className="mb-1 flex items-center gap-2">
          <Label htmlFor={id} className="font-medium">
            {label}
            {required && <span className="ml-1 text-destructive">*</span>}
          </Label>
          {defaultValue !== undefined && !isModified && (
            <Badge
              variant="outline"
              className="text-muted-foreground text-xs font-normal"
            >
              default {JSON.stringify(defaultValue)}
            </Badge>
          )}
          {isModified && (
            <Badge variant="default" className="text-xs font-normal">
              modified
            </Badge>
          )}
          {showReset && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => ctx.onResetField!(id)}
              className="ml-auto h-6 px-2 text-xs text-muted-foreground"
            >
              <RotateCcw className="mr-1 h-3 w-3" />
              reset
            </Button>
          )}
        </div>
      )}
      <div>{children}</div>
      {description}
    </div>
  );
}
