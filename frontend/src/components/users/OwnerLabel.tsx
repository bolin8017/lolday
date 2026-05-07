import { User } from "lucide-react";

interface Props {
  handle: string;
}

export function OwnerLabel({ handle }: Props) {
  return (
    <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
      <User aria-label="user" className="h-3 w-3" />
      {handle}
    </span>
  );
}
