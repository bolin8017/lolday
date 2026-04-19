import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useUpdatePassword } from "@/api/queries/users";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";

const schema = z.object({
  password: z.string().min(8, "At least 8 characters"),
  confirm: z.string(),
}).refine((d) => d.password === d.confirm, { path: ["confirm"], message: "Passwords do not match" });
type Values = z.infer<typeof schema>;

export function PasswordChangeForm() {
  const { register, handleSubmit, reset, formState: { errors, isSubmitting } } = useForm<Values>({
    resolver: zodResolver(schema),
  });
  const mut = useUpdatePassword();
  const { toast } = useToast();
  const onSubmit = handleSubmit(async (v) => {
    await mut.mutateAsync({ password: v.password });
    reset();
    toast({ title: "Password updated." });
  });
  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="pw">New password</Label>
        <Input id="pw" type="password" {...register("password")} />
        {errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}
      </div>
      <div>
        <Label htmlFor="pw2">Confirm password</Label>
        <Input id="pw2" type="password" {...register("confirm")} />
        {errors.confirm && <p className="text-xs text-destructive">{errors.confirm.message}</p>}
      </div>
      <Button type="submit" disabled={isSubmitting}>Update password</Button>
    </form>
  );
}
