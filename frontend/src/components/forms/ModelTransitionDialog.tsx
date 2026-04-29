import { useState } from "react";
import { useTransitionModel, type Stage } from "@/api/queries/models";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  modelName: string;
  version: number;
  currentStage: Stage;
  hasExistingProd: boolean;
}

// Only three targets are selectable; "None" is not a manual transition target.
const TARGET_STAGES: Exclude<Stage, "None">[] = [
  "Staging",
  "Production",
  "Archived",
];

export function ModelTransitionDialog({
  modelName,
  version,
  currentStage,
  hasExistingProd,
}: Props) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<Exclude<Stage, "None">>("Production");
  const [comment, setComment] = useState("");
  const mut = useTransitionModel(modelName);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          Transition
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Transition v{version} from {currentStage}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>Target stage</Label>
            <Select
              value={target}
              onValueChange={(v) => setTarget(v as Exclude<Stage, "None">)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TARGET_STAGES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Comment (optional)</Label>
            <Textarea
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
          {target === "Production" && hasExistingProd && (
            <Alert>
              <AlertDescription>
                Another version is currently Production. It will be
                auto-archived when this one is promoted.
              </AlertDescription>
            </Alert>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            disabled={mut.isPending}
            onClick={async () => {
              await mut.mutateAsync({ version, target_stage: target, comment });
              setOpen(false);
            }}
          >
            Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
