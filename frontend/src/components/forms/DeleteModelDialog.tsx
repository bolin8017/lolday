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

interface Props {
  open: boolean;
  owner: string;
  name: string;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteModelDialog({
  open,
  owner,
  name,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const fullName = `${owner}/${name}`;
  const [confirm, setConfirm] = useState("");

  useEffect(() => {
    if (open) setConfirm("");
  }, [open]);

  const matches = confirm === fullName;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="text-destructive">
            {t("models.delete.title")}
          </DialogTitle>
          <DialogDescription>{t("models.delete.warning")}</DialogDescription>
        </DialogHeader>
        <p className="text-sm">
          {t("models.delete.confirmPrompt", { fullName })}
        </p>
        <Input
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={fullName}
        />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={!matches} onClick={onConfirm}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
