export function JsonViewer({ value }: { value: unknown }) {
  return (
    <pre className="overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
