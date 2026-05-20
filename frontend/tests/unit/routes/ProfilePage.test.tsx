import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import ProfilePage from "@/routes/_authed.profile";

type User = {
  email: string;
  role: "admin" | "developer" | "user" | undefined;
} | null;

const { authState } = vi.hoisted(() => ({
  authState: { currentUser: null as User },
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ currentUser: authState.currentUser }),
}));

// The page composes three Card sections; the third + fourth pull in
// the GitCredentialForm (PAT mask flow) and DiscordIdForm (TanStack
// mutation + zod). Both have their own dedicated unit tests; stub
// them so this test exercises only the page's account-card
// render-from-currentUser logic.
vi.mock("@/components/forms/GitCredentialForm", () => ({
  GitCredentialForm: () => <form data-testid="stub-git-cred-form" />,
}));
vi.mock("@/components/forms/DiscordIdForm", () => ({
  DiscordIdForm: () => <form data-testid="stub-discord-id-form" />,
}));

beforeEach(() => {
  authState.currentUser = {
    email: "lab@test",
    role: "developer",
  };
});

describe("_authed.profile.tsx (ProfilePage)", () => {
  it("renders the email + role from useAuth().currentUser", () => {
    const { getByText } = render(<ProfilePage />);
    expect(getByText(/lab@test/)).toBeInTheDocument();
    // Role label is rendered as plain text next to the "Role:" prefix.
    expect(getByText(/developer/)).toBeInTheDocument();
  });

  it("falls back to role='user' when currentUser exists but role is undefined", () => {
    // Defensive `currentUser?.role ?? "user"` — the auth backend
    // historically returned users without a role field. Pin the
    // fallback so a future refactor doesn't drop it silently.
    authState.currentUser = {
      email: "lab@test",
      role: undefined,
    };
    const { getByText } = render(<ProfilePage />);
    expect(getByText(/user/)).toBeInTheDocument();
  });

  it("renders the three account cards (Account / GitHub PAT / Discord)", () => {
    const { getByText, getByTestId } = render(<ProfilePage />);
    expect(getByText(/Account/)).toBeInTheDocument();
    expect(getByText(/GitHub PAT/)).toBeInTheDocument();
    expect(getByText(/Discord notifications/)).toBeInTheDocument();
    // Sub-forms are mounted (stubbed children render).
    expect(getByTestId("stub-git-cred-form")).toBeInTheDocument();
    expect(getByTestId("stub-discord-id-form")).toBeInTheDocument();
  });

  it("renders the Cloudflare-Access explainer copy under Account", () => {
    // The page calls out the password-change path is GitHub, not
    // lolday — important UX guarantee for users hunting a password
    // reset. Pin so a refactor doesn't silently delete the helper text.
    const { getByText } = render(<ProfilePage />);
    expect(
      getByText(/password changes happen at your GitHub account/i),
    ).toBeInTheDocument();
  });
});
