import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useLogin } from "@/api/queries/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Password is required"),
});
type FormValues = z.infer<typeof schema>;

export function LoginForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const login = useLogin();
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<FormValues>({
    resolver: zodResolver(schema),
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      await login.mutateAsync(values);
      navigate("/", { replace: true });
    } catch {
      // error surfaced via login.isError below
    }
  });

  const serverError = login.isError
    ? ((login.error as { status?: number }).status === 429
        ? t("login.rateLimited")
        : t("login.invalidCredentials"))
    : null;

  return (
    <Card className="w-[380px]">
      <CardHeader>
        <CardTitle>{t("login.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="space-y-2">
            <Label htmlFor="email">{t("login.email")}</Label>
            <Input id="email" type="email" autoComplete="email" {...register("email")} />
            {errors.email && <p className="text-xs text-destructive">{errors.email.message}</p>}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">{t("login.password")}</Label>
            <Input id="password" type="password" autoComplete="current-password" {...register("password")} />
            {errors.password && <p className="text-xs text-destructive">{errors.password.message}</p>}
          </div>
          {serverError && (
            <Alert variant="destructive"><AlertDescription>{serverError}</AlertDescription></Alert>
          )}
          <Button type="submit" className="w-full" disabled={isSubmitting || login.isPending}>
            {t("login.submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
