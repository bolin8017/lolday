import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useCreateDataset } from "@/api/queries/datasets";
import { parseCsvPreview, type CsvPreview } from "@/lib/csv";
import { checkCsvSize } from "./DatasetUploadForm.logic";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";

const schema = z.object({
  name: z.string().min(1).max(100),
  description: z.string().optional(),
  visibility: z.enum(["public", "private"]),
  csv_content: z.string().min(1, "CSV content is required"),
});
type Values = z.infer<typeof schema>;

export function DatasetUploadForm() {
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

  async function onFilePick(ev: React.ChangeEvent<HTMLInputElement>) {
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
      const ds = await mut.mutateAsync(v);
      nav(`/datasets/${ds.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });

  return (
    <form className="space-y-4 max-w-2xl" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name</Label>
        <Input id="name" placeholder="upx-train-v3" {...register("name")} />
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
        <select
          id="visibility"
          className="block w-full rounded-md border p-2"
          {...register("visibility")}
        >
          <option value="public">Public (all lab members)</option>
          <option value="private">Private (me + admin)</option>
        </select>
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
          <p className="text-xs text-destructive">{errors.csv_content.message}</p>
        )}
        {parseError && (
          <Alert variant="destructive">
            <AlertDescription>{parseError}</AlertDescription>
          </Alert>
        )}
        {preview && (
          <div className="rounded border p-2 text-xs">
            <p className="text-muted-foreground mb-1">
              Preview ({preview.rows.length} of {preview.totalRows} rows)
            </p>
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
        )}
      </div>

      <Button type="submit" disabled={isSubmitting || !!parseError}>
        Upload dataset
      </Button>
    </form>
  );
}
