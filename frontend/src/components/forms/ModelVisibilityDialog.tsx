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
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  current: "public" | "private";
  onClose: () => void;
  onSubmit: (visibility: "public" | "private", comment: string | null) => void;
}

export function ModelVisibilityDialog({
  open,
  current,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (open) setComment("");
  }, [open]);

  const target: "public" | "private" =
    current === "public" ? "private" : "public";
  const titleKey =
    target === "public"
      ? "models.visibility.makePublic"
      : "models.visibility.makePrivate";
  const warningKey =
    target === "public"
      ? "models.visibility.warningPublic"
      : "models.visibility.warningPrivate";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t(titleKey)}</DialogTitle>
          <DialogDescription>{t(warningKey)}</DialogDescription>
        </DialogHeader>
        <Textarea
          placeholder="Optional comment"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={3}
        />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(target, comment || null)}>
            {t(titleKey)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
