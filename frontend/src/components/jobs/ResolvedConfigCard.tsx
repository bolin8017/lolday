import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonTreeView } from "@/components/common/JsonTreeView";
import { UserParamsTable } from "./UserParamsTable";

interface Props {
  resolvedConfig: Record<string, unknown>;
  userParams?: Record<string, unknown> | null;
  detectorDefaults?: Record<string, unknown> | null;
}

export function ResolvedConfigCard({
  resolvedConfig,
  userParams = null,
  detectorDefaults = null,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const lineCount = JSON.stringify(resolvedConfig, null, 2).split("\n").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Resolved config</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <h3 className="mb-2 text-sm font-medium">Your hyperparameters</h3>
          {userParams !== null ? (
            <UserParamsTable
              userParams={userParams}
              defaults={detectorDefaults}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              Legacy job — user-supplied params not recorded.
            </p>
          )}
        </div>

        <div>
          <button
            type="button"
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
            onClick={() => setExpanded((x) => !x)}
          >
            {expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            {expanded ? "Hide" : "Show"} full resolved config ({lineCount}{" "}
            lines)
          </button>
          {expanded && (
            <div className="mt-2">
              <JsonTreeView value={resolvedConfig} collapsed={1} />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
