import { useState } from "react";
import { client } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { Folder, FileText, Download } from "lucide-react";
import { cn } from "@/lib/cn";

interface Entry {
  path: string;
  is_dir: boolean;
  file_size: number;
}

function useArtifacts(runId: string, path: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "artifacts", path ?? ""],
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/runs/{run_id}/artifacts",
        {
          params: { path: { run_id: runId }, query: path ? { path } : {} },
        },
      );
      if (error) throw error;
      return (data as { files?: Entry[] }).files ?? [];
    },
  });
}

function TreeLevel({
  runId,
  path,
  depth,
}: {
  runId: string;
  path: string | null;
  depth: number;
}) {
  const { data, isLoading } = useArtifacts(runId, path);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  if (isLoading)
    return <p className="ml-4 text-xs text-muted-foreground">Loading…</p>;
  if (!data || data.length === 0)
    return <p className="ml-4 text-xs text-muted-foreground">(empty)</p>;
  if (depth >= 10)
    return <p className="ml-4 text-xs text-destructive">(tree too deep)</p>;

  return (
    <ul className="space-y-1">
      {data.map((e) => {
        const name = e.path.split("/").pop() ?? e.path;
        const isExpanded = expanded.has(e.path);
        return (
          <li key={e.path} className={cn("rounded px-2", depth > 0 && "ml-4")}>
            <div className="flex items-center gap-2 py-1">
              {e.is_dir ? (
                <button
                  className="flex items-center gap-1 text-sm hover:underline"
                  onClick={() =>
                    setExpanded((s) => {
                      const n = new Set(s);
                      if (n.has(e.path)) n.delete(e.path);
                      else n.add(e.path);
                      return n;
                    })
                  }
                >
                  <Folder className="h-4 w-4" /> {name}
                </button>
              ) : (
                <>
                  <FileText className="h-4 w-4" />
                  <span className="flex-1 text-sm">{name}</span>
                  <a
                    className="inline-flex items-center text-xs text-primary hover:underline"
                    href={`/api/v1/runs/${runId}/artifacts/download?path=${encodeURIComponent(e.path)}`}
                    download={name}
                  >
                    <Download className="mr-1 h-3 w-3" />
                    download
                  </a>
                </>
              )}
            </div>
            {e.is_dir && isExpanded && (
              <TreeLevel runId={runId} path={e.path} depth={depth + 1} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ArtifactTree({ runId }: { runId: string }) {
  return <TreeLevel runId={runId} path={null} depth={0} />;
}
