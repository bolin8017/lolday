import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useCurrentUser } from "@/api/queries/auth";
import { useUpdateMe } from "@/api/queries/users";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";

const schema = z.object({
  discord_user_id: z
    .string()
    .regex(
      /^\d{15,20}$/u,
      "Discord IDs are 15–20 digits (enable Developer Mode in Discord, then right-click your name → Copy User ID)",
    )
    .or(z.literal("")),
});
type Values = z.infer<typeof schema>;

export function DiscordIdForm() {
  const me = useCurrentUser();
  const update = useUpdateMe();
  const { toast } = useToast();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { discord_user_id: "" },
  });

  useEffect(() => {
    if (me.data) reset({ discord_user_id: me.data.discord_user_id ?? "" });
  }, [me.data, reset]);

  const onSubmit = handleSubmit(async (v) => {
    const next =
      v.discord_user_id.trim() === "" ? null : v.discord_user_id.trim();
    await update.mutateAsync({ discord_user_id: next });
    toast({ title: next ? "Discord ID saved." : "Discord ID cleared." });
  });

  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="discord_user_id">Discord User ID (optional)</Label>
        <Input
          id="discord_user_id"
          inputMode="numeric"
          placeholder="987654321098765432"
          {...register("discord_user_id")}
        />
        {errors.discord_user_id && (
          <p className="text-xs text-destructive">
            {errors.discord_user_id.message}
          </p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Set this to receive direct @mentions in{" "}
          <code>#lolday-alerts-events</code>. Leave blank to opt out of pings
          (you'll still see your name as plain text).
        </p>
      </div>
      <div className="sticky bottom-0 -mx-4 flex justify-end border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:-mx-6 sm:px-6 sm:pb-3">
        <Button type="submit" disabled={isSubmitting} className="h-11">
          Save
        </Button>
      </div>
    </form>
  );
}
