import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  open: boolean;
  initialValue: string | null;
  onClose: () => void;
  onSubmit: (description: string) => void;
}

export function ModelDescriptionEditor({
  open,
  initialValue,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initialValue ?? "");

  // Re-sync when dialog re-opens with different initial value
  useEffect(() => {
    if (open) setValue(initialValue ?? "");
  }, [open, initialValue]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.description.edit")}</DialogTitle>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("models.description.placeholder")}
          rows={10}
          maxLength={5000}
        />
        <p className="text-xs text-muted-foreground">{value.length} / 5000</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(value)}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
