import ReactJsonView from "@microlink/react-json-view";

interface Props {
  value: unknown;
  collapsed?: number | boolean;
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  return (
    <div className="overflow-auto rounded-md border bg-card">
      <ReactJsonView
        src={(value ?? {}) as object}
        name={false}
        collapsed={collapsed}
        displayDataTypes={false}
        displayObjectSize={false}
        enableClipboard={copyable}
        theme="rjv-default"
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
