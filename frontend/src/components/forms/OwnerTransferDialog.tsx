import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (newOwner: string, comment: string | null) => void;
}

export function OwnerTransferDialog({ open, onClose, onSubmit }: Props) {
  const { t } = useTranslation();
  const [handle, setHandle] = useState("");
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (open) {
      setHandle("");
      setComment("");
    }
  }, [open]);

  const valid = handle.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.transfer.title")}</DialogTitle>
          <DialogDescription>
            {t("models.transfer.description")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="new-owner">
            {t("models.transfer.newOwnerLabel")}
          </label>
          <Input
            id="new-owner"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            autoFocus
          />
        </div>
        <Textarea
          placeholder="Optional comment"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={3}
        />
        <p className="text-sm text-amber-600">{t("models.transfer.warning")}</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!valid}
            onClick={() => onSubmit(handle.trim(), comment || null)}
          >
            {t("models.transfer.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
