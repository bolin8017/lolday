import { useState } from "react";
import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface ErrorBanner {
  code?: string;
  message?: ReactNode;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmText: string;
  onConfirm: () => void | Promise<void>;
  pending: boolean;
  errorBanner: ErrorBanner | null;
}

export function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText,
  onConfirm,
  pending,
  errorBanner,
}: Props) {
  const [typed, setTyped] = useState("");
  const matches = typed === confirmText;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!pending) onOpenChange(o);
        if (!o) setTyped("");
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-destructive">{title}</DialogTitle>
          <DialogDescription asChild>
            <div className="text-sm text-muted-foreground">{description}</div>
          </DialogDescription>
        </DialogHeader>

        {errorBanner ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {errorBanner.message ?? errorBanner.code ?? "Delete failed."}
          </div>
        ) : null}

        <div className="space-y-2 py-2">
          <Label htmlFor="delete-confirm-input">
            Type <span className="font-mono font-semibold">{confirmText}</span> to confirm
          </Label>
          <Input
            id="delete-confirm-input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={confirmText}
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || pending}
            onClick={() => onConfirm()}
          >
            {pending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
