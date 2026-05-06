import { type ChangeEvent, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateDataset } from "@/api/queries/datasets";
import { parseCsvPreview, type CsvPreview } from "@/lib/csv";
import { checkCsvSize } from "./DatasetUploadForm.logic";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";
import { StickyFormFooter } from "./StickyFormFooter";

const schema = z.object({
  name: z.string().min(1).max(100),
  description: z.string().optional(),
  visibility: z.enum(["public", "private"]),
  csv_content: z.string().min(1, "CSV content is required"),
});
type Values = z.infer<typeof schema>;

export function DatasetUploadForm() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const mut = useCreateDataset();
  const {
    register,
    handleSubmit,
    setValue,
    setError,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { visibility: "public" },
  });
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const content = watch("csv_content");
  const visibility = watch("visibility");

  async function onFilePick(ev: ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setValue("csv_content", text, { shouldValidate: true });
    runPreview(text);
  }

  function runPreview(text: string) {
    setParseError(null);
    const sizeErr = checkCsvSize(text);
    if (sizeErr) {
      setParseError(sizeErr);
      setPreview(null);
      return;
    }
    try {
      setPreview(parseCsvPreview(text, 10));
    } catch (e) {
      setParseError((e as Error).message);
      setPreview(null);
    }
  }

  const onSubmit = handleSubmit(async (v) => {
    const sizeErr = checkCsvSize(v.csv_content);
    if (sizeErr) {
      setError("csv_content", { message: sizeErr });
      return;
    }
    try {
      // Validate every row before POST. limit=1 only caps the returned preview slice;
      // the validation loop inside parseCsvPreview always walks the whole file.
      parseCsvPreview(v.csv_content, 1);
    } catch (e) {
      setError("csv_content", { message: (e as Error).message });
      return;
    }
    try {
      const ds = await mut.mutateAsync(v);
      nav(`/datasets/${ds.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });

  return (
    <form className="max-w-2xl space-y-4" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          placeholder={t("datasets.new.namePlaceholder")}
          {...register("name")}
        />
        {errors.name && (
          <p className="text-xs text-destructive">{errors.name.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" rows={2} {...register("description")} />
      </div>
      <div>
        <Label htmlFor="visibility">Visibility</Label>
        <Select
          value={visibility}
          onValueChange={(v) =>
            setValue("visibility", v as "public" | "private", {
              shouldValidate: true,
            })
          }
        >
          <SelectTrigger id="visibility">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="public">Public (all lab members)</SelectItem>
            <SelectItem value="private">Private (me + admin)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>CSV content</Label>
        <Tabs defaultValue="file">
          <TabsList>
            <TabsTrigger value="file">File picker</TabsTrigger>
            <TabsTrigger value="paste">Paste</TabsTrigger>
          </TabsList>
          <TabsContent value="file">
            <Input type="file" accept=".csv,text/csv" onChange={onFilePick} />
          </TabsContent>
          <TabsContent value="paste">
            <Textarea
              rows={8}
              placeholder={"file_name,label,family\nabc…,Malware,mirai"}
              value={content ?? ""}
              onChange={(e) => {
                setValue("csv_content", e.target.value);
                runPreview(e.target.value);
              }}
            />
          </TabsContent>
        </Tabs>
        {errors.csv_content && (
          <p className="text-xs text-destructive">
            {errors.csv_content.message}
          </p>
        )}
        {parseError && (
          <Alert variant="destructive">
            <AlertDescription>{parseError}</AlertDescription>
          </Alert>
        )}
        {preview && (
          <div className="rounded border p-2 text-xs">
            <p className="mb-1 text-muted-foreground">
              Preview ({preview.rows.length} of {preview.totalRows} rows)
            </p>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    {preview.columns.map((c) => (
                      <th key={c} className="text-left">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => (
                    <tr key={i}>
                      {preview.columns.map((c) => (
                        <td key={c} className="truncate">
                          {r[c]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <StickyFormFooter>
        <Button
          type="button"
          variant="ghost"
          className="h-11"
          onClick={() => nav(-1)}
        >
          {t("common.cancel")}
        </Button>
        <Button
          type="submit"
          disabled={isSubmitting || !!parseError}
          className="h-11"
        >
          {isSubmitting
            ? t("datasets.new.submitting")
            : t("datasets.new.submitLabel")}
        </Button>
      </StickyFormFooter>
    </form>
  );
}
