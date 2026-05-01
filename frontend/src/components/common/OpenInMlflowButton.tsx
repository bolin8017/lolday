import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  experimentId?: string;
  runId?: string;
  size?: "default" | "sm";
}

export function OpenInMlflowButton({
  experimentId,
  runId,
  size = "sm",
}: Props) {
  let href = "/mlflow/";
  if (experimentId && runId) {
    href = `/mlflow/#/experiments/${experimentId}/runs/${runId}`;
  } else if (experimentId) {
    href = `/mlflow/#/experiments/${experimentId}`;
  }
  return (
    <Button asChild variant="outline" size={size}>
      <a href={href} target="_blank" rel="noopener noreferrer">
        <ExternalLink className="mr-2 h-4 w-4" />
        Open in MLflow
      </a>
    </Button>
  );
}
