import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { gitCredState, setMock, clearMock, toastMock } = vi.hoisted(() => ({
  gitCredState: {
    data: undefined as { provider: string } | undefined | null,
    isLoading: false,
  },
  setMock: vi.fn(),
  clearMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/api/queries/users", () => ({
  useGitCredential: () => gitCredState,
  useSetGitCredential: () => ({ mutateAsync: setMock }),
  useDeleteGitCredential: () => ({ mutateAsync: clearMock }),
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

import { GitCredentialForm } from "@/components/forms/GitCredentialForm";

beforeEach(() => {
  gitCredState.data = undefined;
  gitCredState.isLoading = false;
  setMock.mockReset();
  setMock.mockResolvedValue(undefined);
  clearMock.mockReset();
  clearMock.mockResolvedValue(undefined);
  toastMock.mockReset();
});

describe("GitCredentialForm", () => {
  it("renders Loading… while the credential query is in flight", () => {
    gitCredState.isLoading = true;
    render(<GitCredentialForm />);
    expect(screen.getByText(/Loading…/)).toBeInTheDocument();
  });

  it("renders the read-only masked alert when a credential is set", () => {
    gitCredState.data = { provider: "github" };
    render(<GitCredentialForm />);
    expect(
      screen.getByText(/GitHub PAT is set \(masked\)/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Update/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Clear/ })).toBeInTheDocument();
  });

  it("calls the clear-credential mutation when Clear is clicked", async () => {
    const user = userEvent.setup();
    gitCredState.data = { provider: "github" };
    render(<GitCredentialForm />);
    await user.click(screen.getByRole("button", { name: /Clear/ }));
    expect(clearMock).toHaveBeenCalled();
    expect(toastMock).toHaveBeenCalledWith({ title: "Credential cleared." });
  });

  it("switches to edit mode when Update is clicked", async () => {
    const user = userEvent.setup();
    gitCredState.data = { provider: "github" };
    render(<GitCredentialForm />);
    await user.click(screen.getByRole("button", { name: /Update/ }));
    // The form input + Cancel button should now be visible.
    expect(screen.getByLabelText(/GitHub PAT/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
  });

  it("renders the form directly when no credential is set", () => {
    gitCredState.data = null;
    render(<GitCredentialForm />);
    expect(screen.getByLabelText(/GitHub PAT/)).toBeInTheDocument();
    // Cancel is not shown when the form opens directly (editing flag is
    // false; Cancel only appears on the Update-from-existing flow).
    expect(
      screen.queryByRole("button", { name: /Cancel/ }),
    ).not.toBeInTheDocument();
  });

  it("shows the 'too short' error when the token is below 20 characters", async () => {
    const user = userEvent.setup();
    gitCredState.data = null;
    render(<GitCredentialForm />);
    await user.type(screen.getByLabelText(/GitHub PAT/), "ghp_short");
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(
      await screen.findByText(/Looks too short for a PAT/),
    ).toBeInTheDocument();
    expect(setMock).not.toHaveBeenCalled();
  });

  it("submits the PAT with provider='github' when valid", async () => {
    const user = userEvent.setup();
    gitCredState.data = null;
    render(<GitCredentialForm />);
    const token = "ghp_" + "a".repeat(36);
    await user.type(screen.getByLabelText(/GitHub PAT/), token);
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(setMock).toHaveBeenCalledWith({ provider: "github", token });
    expect(toastMock).toHaveBeenCalledWith({ title: "GitHub PAT saved." });
  });
});
