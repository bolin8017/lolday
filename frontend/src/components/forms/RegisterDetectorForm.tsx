import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useRegisterDetector } from "@/api/queries/detectors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";
import { StickyFormFooter } from "./StickyFormFooter";

const schema = z.object({
  name: z
    .string()
    .min(1)
    .regex(/^[a-z0-9-]+$/, "lowercase letters, digits, hyphen only"),
  display_name: z.string().min(1).max(200),
  description: z.string().optional(),
  git_url: z.string().url(),
});
type Values = z.infer<typeof schema>;

export function RegisterDetectorForm() {
  const nav = useNavigate();
  const mut = useRegisterDetector();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
  });
  const onSubmit = handleSubmit(async (v) => {
    try {
      const det = await mut.mutateAsync(v);
      nav(`/detectors/${det.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });
  return (
    <form className="space-y-4 max-w-xl" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name (slug)</Label>
        <Input id="name" placeholder="upxelfdet" {...register("name")} />
        {errors.name && (
          <p className="text-xs text-destructive">{errors.name.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="display_name">Display name</Label>
        <Input
          id="display_name"
          placeholder="UPX ELF Detector"
          {...register("display_name")}
        />
        {errors.display_name && (
          <p className="text-xs text-destructive">
            {errors.display_name.message}
          </p>
        )}
      </div>
      <div>
        <Label htmlFor="git_url">Git URL</Label>
        <Input
          id="git_url"
          placeholder="https://github.com/…"
          {...register("git_url")}
        />
        {errors.git_url && (
          <p className="text-xs text-destructive">{errors.git_url.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" rows={3} {...register("description")} />
      </div>
      <StickyFormFooter>
        <Button type="submit" disabled={isSubmitting} className="h-11">
          Register detector
        </Button>
      </StickyFormFooter>
    </form>
  );
}
