import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/hooks/use-toast";

const TagsSchema = z.record(z.string(), z.string());

interface Props {
  open: boolean;
  initialValue: Record<string, string>;
  onClose: () => void;
  onSubmit: (tags: Record<string, string>) => void;
}

export function ModelTagsEditor({
  open,
  initialValue,
  onClose,
  onSubmit,
}: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(
    JSON.stringify(initialValue ?? {}, null, 2),
  );

  useEffect(() => {
    if (open) setValue(JSON.stringify(initialValue ?? {}, null, 2));
  }, [open, initialValue]);

  const handleSubmit = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch {
      toast({ title: t("models.tags.schemaError"), variant: "destructive" });
      return;
    }
    const result = TagsSchema.safeParse(parsed);
    if (!result.success) {
      toast({ title: t("models.tags.schemaError"), variant: "destructive" });
      return;
    }
    onSubmit(result.data);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.tags.edit")}</DialogTitle>
        </DialogHeader>
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={t("models.tags.placeholder")}
          rows={10}
          className="font-mono text-sm"
        />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
