import { useAuth } from "@/hooks/useAuth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { GitCredentialForm } from "@/components/forms/GitCredentialForm";
import { DiscordIdForm } from "@/components/forms/DiscordIdForm";

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
          <p className="pt-2 text-xs text-muted-foreground">
            Authenticated via Cloudflare Access — password changes happen at your GitHub account, not here.
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>GitHub PAT</CardTitle></CardHeader>
        <CardContent><GitCredentialForm /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Discord notifications</CardTitle></CardHeader>
        <CardContent><DiscordIdForm /></CardContent>
      </Card>
    </div>
  );
}
