import { Switch } from "@/components/ui/switch";
import type { WidgetProps } from "@rjsf/utils";

export function SwitchWidget(props: WidgetProps) {
  const { value, onChange, disabled, readonly, id } = props;
  return (
    <Switch
      id={id}
      checked={!!value}
      onCheckedChange={(c) => onChange(c)}
      disabled={disabled || readonly}
    />
  );
}
