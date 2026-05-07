import ReactJsonView from "@microlink/react-json-view";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

interface Props {
  value: unknown;
  collapsed?: number | boolean;
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  const theme = useResolvedTheme();
  // monokai is one of the bundled dark themes in @microlink/react-json-view
  // and gives high-contrast keys/values on dark backgrounds.
  return (
    <div className="overflow-auto rounded-md border bg-card">
      <ReactJsonView
        src={(value ?? {}) as object}
        name={false}
        collapsed={collapsed}
        displayDataTypes={false}
        displayObjectSize={false}
        enableClipboard={copyable}
        theme={theme === "dark" ? "monokai" : "rjv-default"}
        style={{
          padding: "0.75rem",
          fontSize: "0.8rem",
          fontFamily: "ui-monospace, monospace",
          background: "transparent",
        }}
      />
    </div>
  );
}
