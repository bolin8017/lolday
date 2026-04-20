import { useAuth } from "@/hooks/useAuth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PasswordChangeForm } from "@/components/forms/PasswordChangeForm";
import { GitCredentialForm } from "@/components/forms/GitCredentialForm";

export const handle = { breadcrumb: "Profile" };

export default function ProfilePage() {
  const { currentUser } = useAuth();
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <Card>
        <CardHeader><CardTitle>Account</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div><span className="text-muted-foreground">Email:</span> {currentUser?.email}</div>
          <div><span className="text-muted-foreground">Role:</span> {currentUser?.role ?? "user"}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Change password</CardTitle></CardHeader>
        <CardContent><PasswordChangeForm /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>GitHub PAT</CardTitle></CardHeader>
        <CardContent><GitCredentialForm /></CardContent>
      </Card>
    </div>
  );
}
