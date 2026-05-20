import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { registerMock, navigateMock, applyFieldErrorsMock } = vi.hoisted(() => ({
  registerMock: vi.fn(),
  navigateMock: vi.fn(),
  applyFieldErrorsMock: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/api/queries/detectors", () => ({
  useRegisterDetector: () => ({ mutateAsync: registerMock }),
}));

vi.mock("@/lib/errors", () => ({
  applyFieldErrorsToForm: applyFieldErrorsMock,
}));

import { RegisterDetectorForm } from "@/components/forms/RegisterDetectorForm";

function renderForm() {
  return render(
    <MemoryRouter>
      <RegisterDetectorForm />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  registerMock.mockReset();
  navigateMock.mockReset();
  applyFieldErrorsMock.mockReset();
});

describe("RegisterDetectorForm", () => {
  it("rejects names that contain uppercase or punctuation", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText(/Name \(slug\)/), "Bad_Name");
    await user.type(screen.getByLabelText(/Display name/), "Bad");
    await user.type(
      screen.getByLabelText(/Git URL/),
      "https://example.com/repo.git",
    );
    await user.click(screen.getByRole("button", { name: /Register detector/ }));
    expect(
      await screen.findByText(/lowercase letters, digits, hyphen only/),
    ).toBeInTheDocument();
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("rejects a non-URL git_url", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText(/Name \(slug\)/), "upxelfdet");
    await user.type(screen.getByLabelText(/Display name/), "UPX ELF Detector");
    await user.type(screen.getByLabelText(/Git URL/), "not-a-url");
    await user.click(screen.getByRole("button", { name: /Register detector/ }));
    // The default zod URL error message is locale-specific; pin the field
    // by name rather than the message string.
    expect(registerMock).not.toHaveBeenCalled();
  });

  it("submits the form and navigates to the new detector on success", async () => {
    const user = userEvent.setup();
    registerMock.mockResolvedValue({ id: "det-xyz" });
    renderForm();
    await user.type(screen.getByLabelText(/Name \(slug\)/), "upxelfdet");
    await user.type(screen.getByLabelText(/Display name/), "UPX ELF Detector");
    await user.type(
      screen.getByLabelText(/Git URL/),
      "https://github.com/bolin8017/upxelfdet.git",
    );
    await user.click(screen.getByRole("button", { name: /Register detector/ }));
    expect(registerMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "upxelfdet",
        display_name: "UPX ELF Detector",
        git_url: "https://github.com/bolin8017/upxelfdet.git",
      }),
    );
    expect(navigateMock).toHaveBeenCalledWith("/detectors/det-xyz");
  });

  it("routes server-side validation errors through applyFieldErrorsToForm", async () => {
    const user = userEvent.setup();
    const serverError = new Error("server validation") as Error & {
      structuredDetail: unknown;
    };
    registerMock.mockRejectedValue(serverError);
    renderForm();
    await user.type(screen.getByLabelText(/Name \(slug\)/), "upxelfdet");
    await user.type(screen.getByLabelText(/Display name/), "UPX ELF Detector");
    await user.type(
      screen.getByLabelText(/Git URL/),
      "https://github.com/bolin8017/upxelfdet.git",
    );
    await user.click(screen.getByRole("button", { name: /Register detector/ }));
    // applyFieldErrorsToForm is the seam where backend 422-shaped errors
    // get redistributed onto the form fields; pin the call signature.
    expect(applyFieldErrorsMock).toHaveBeenCalledTimes(1);
    expect(applyFieldErrorsMock.mock.calls[0][0]).toBe(serverError);
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
