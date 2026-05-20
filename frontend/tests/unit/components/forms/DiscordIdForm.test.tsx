import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { meState, updateMock, toastMock } = vi.hoisted(() => ({
  meState: {
    data: undefined as { discord_user_id: string | null } | undefined,
  },
  updateMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("@/api/queries/auth", () => ({
  useCurrentUser: () => meState,
}));

vi.mock("@/api/queries/users", () => ({
  useUpdateMe: () => ({ mutateAsync: updateMock }),
}));

vi.mock("@/hooks/use-toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

import { DiscordIdForm } from "@/components/forms/DiscordIdForm";

beforeEach(() => {
  meState.data = undefined;
  updateMock.mockReset();
  updateMock.mockResolvedValue(undefined);
  toastMock.mockReset();
});

describe("DiscordIdForm", () => {
  it("pre-fills the input with the current me.data.discord_user_id", () => {
    meState.data = { discord_user_id: "123456789012345678" };
    render(<DiscordIdForm />);
    expect(screen.getByLabelText(/Discord User ID/)).toHaveValue(
      "123456789012345678",
    );
  });

  it("treats null discord_user_id as an empty input", () => {
    meState.data = { discord_user_id: null };
    render(<DiscordIdForm />);
    expect(screen.getByLabelText(/Discord User ID/)).toHaveValue("");
  });

  it("shows the format-help error for a non-numeric value on submit", async () => {
    const user = userEvent.setup();
    meState.data = { discord_user_id: null };
    render(<DiscordIdForm />);
    await user.type(screen.getByLabelText(/Discord User ID/), "not-a-number");
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(
      await screen.findByText(/Discord IDs are 15–20 digits/),
    ).toBeInTheDocument();
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("submits null when the input is cleared (opt-out path)", async () => {
    const user = userEvent.setup();
    meState.data = { discord_user_id: "123456789012345678" };
    render(<DiscordIdForm />);
    await user.clear(screen.getByLabelText(/Discord User ID/));
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(updateMock).toHaveBeenCalledWith({ discord_user_id: null });
    expect(toastMock).toHaveBeenCalledWith({
      title: "Discord ID cleared.",
    });
  });

  it("submits the value when a valid Discord ID is entered", async () => {
    const user = userEvent.setup();
    meState.data = { discord_user_id: null };
    render(<DiscordIdForm />);
    await user.type(
      screen.getByLabelText(/Discord User ID/),
      "987654321098765432",
    );
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(updateMock).toHaveBeenCalledWith({
      discord_user_id: "987654321098765432",
    });
    expect(toastMock).toHaveBeenCalledWith({ title: "Discord ID saved." });
  });
});
