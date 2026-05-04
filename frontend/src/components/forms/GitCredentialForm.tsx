import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  useGitCredential,
  useSetGitCredential,
  useDeleteGitCredential,
} from "@/api/queries/users";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/hooks/use-toast";

const schema = z.object({
  token: z.string().min(20, "Looks too short for a PAT"),
});
type Values = z.infer<typeof schema>;

export function GitCredentialForm() {
  const { data: cred, isLoading } = useGitCredential();
  const setCred = useSetGitCredential();
  const clearCred = useDeleteGitCredential();
  const [editing, setEditing] = useState(false);
  const { toast } = useToast();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
  });

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;

  if (cred && !editing) {
    return (
      <div className="space-y-3">
        <Alert>
          <AlertDescription>
            GitHub PAT is set (masked). Needed for detector builds.
          </AlertDescription>
        </Alert>
        <div className="sticky bottom-0 -mx-4 flex justify-end gap-2 border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:-mx-6 sm:px-6 sm:pb-3">
          <Button
            variant="secondary"
            onClick={() => setEditing(true)}
            className="h-11"
          >
            Update
          </Button>
          <Button
            variant="destructive"
            className="h-11"
            onClick={async () => {
              await clearCred.mutateAsync();
              toast({ title: "Credential cleared." });
            }}
          >
            Clear
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="space-y-3"
      onSubmit={handleSubmit(async (v) => {
        await setCred.mutateAsync({ provider: "github", token: v.token });
        reset();
        setEditing(false);
        toast({ title: "GitHub PAT saved." });
      })}
    >
      <div>
        <Label htmlFor="tok">GitHub PAT</Label>
        <Input
          id="tok"
          type="password"
          autoComplete="off"
          {...register("token")}
        />
        {errors.token && (
          <p className="text-xs text-destructive">{errors.token.message}</p>
        )}
      </div>
      <div className="sticky bottom-0 -mx-4 flex justify-end gap-2 border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:-mx-6 sm:px-6 sm:pb-3">
        {editing && (
          <Button
            type="button"
            variant="ghost"
            className="h-11"
            onClick={() => setEditing(false)}
          >
            Cancel
          </Button>
        )}
        <Button type="submit" disabled={isSubmitting} className="h-11">
          Save
        </Button>
      </div>
    </form>
  );
}
