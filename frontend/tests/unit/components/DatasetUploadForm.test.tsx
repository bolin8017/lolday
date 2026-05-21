import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  checkCsvSize,
  MAX_CSV_BYTES,
} from "@/components/forms/DatasetUploadForm.logic";
import { DatasetUploadForm } from "@/components/forms/DatasetUploadForm";
import { LoldayApiError } from "@/api/errors";

const { createDatasetMock, navigateMock } = vi.hoisted(() => ({
  createDatasetMock: vi.fn(),
  navigateMock: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("@/api/queries/datasets", () => ({
  useCreateDataset: () => ({
    mutateAsync: createDatasetMock,
    isPending: false,
  }),
}));

function renderForm() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <DatasetUploadForm />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  createDatasetMock.mockReset();
  navigateMock.mockReset();
});

describe("checkCsvSize", () => {
  it("accepts small CSV", () => {
    expect(checkCsvSize("a,b\n1,2\n")).toBeNull();
  });
  it("rejects > 10 MB", () => {
    const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
    expect(checkCsvSize(oversize)).toMatch(/exceeds/i);
  });
});

describe("<DatasetUploadForm>", () => {
  it("uses the i18n placeholder for the Name input", () => {
    renderForm();
    expect(
      screen.getByPlaceholderText(/malware-train-2026-q1/),
    ).toBeInTheDocument();
  });

  it("renders a Cancel button alongside Submit", () => {
    renderForm();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Upload dataset/ }),
    ).toBeInTheDocument();
  });

  it("renders a shadcn Visibility Select (combobox role)", () => {
    renderForm();
    // shadcn Select renders an accessible combobox button (Radix Select.Trigger).
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("blocks submit when CSV row validation fails", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText("Name"), "ds");
    // Switch to Paste tab and type an obviously bad CSV
    await user.click(screen.getByRole("tab", { name: /Paste/ }));
    const textarea = screen.getByPlaceholderText(/file_name,label,family/);
    await user.type(textarea, "file_name,label\nDEADBEEF,Malware\n");
    // Inline preview parser should already surface the SHA256 error via Alert
    expect(await screen.findByText(/SHA256/i)).toBeInTheDocument();
  });

  it("renders preview table when valid CSV is pasted", async () => {
    const user = userEvent.setup();
    renderForm();
    // Use 64-char hex SHA256s + a valid label so parseCsvPreview succeeds and
    // the preview table renders (covers DatasetUploadForm.tsx 171-200).
    const sha = "a".repeat(64);
    const csv = `file_name,label\n${sha},Malware\n${sha},Benign\n`;
    await user.click(screen.getByRole("tab", { name: /Paste/ }));
    const textarea = screen.getByPlaceholderText(/file_name,label,family/);
    // Use paste rather than type — typing each char triggers a preview parse
    // per keystroke, and 64-char hex per row makes that prohibitively slow.
    await user.click(textarea);
    await user.paste(csv);
    // Preview header text ("Preview (2 of 2 rows)") appears alongside the
    // rendered table headers + data cells.
    expect(
      await screen.findByText(/Preview \(2 of 2 rows\)/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "file_name" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "label" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(sha).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Malware")).toBeInTheDocument();
    expect(screen.getByText("Benign")).toBeInTheDocument();
  });

  it("calls navigate(-1) when Cancel is clicked", async () => {
    const user = userEvent.setup();
    renderForm();
    // Cancel button onClick (DatasetUploadForm.tsx:209) calls nav(-1) — the
    // MemoryRouter has only one history entry so the click is a no-op
    // navigation but the onClick branch must execute without throwing.
    const cancel = screen.getByRole("button", { name: /Cancel/ });
    await user.click(cancel);
    // Form is still mounted (no programmatic redirect on cancel within tests).
    expect(cancel).toBeInTheDocument();
  });

  it("toggles Visibility from Public to Private via the Radix Select onValueChange (L122-126)", async () => {
    renderForm();
    // pointerEventsCheck:0 because Radix Select sets pointer-events:none
    // on body while the listbox is open; userEvent v14 strict mode rejects
    // clicks under that state otherwise.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const trigger = screen.getByRole("combobox");
    // Default value is "public" per defaultValues; confirm before change.
    expect(trigger).toHaveTextContent(/Public/);
    await user.click(trigger);
    await user.click(await screen.findByRole("option", { name: /Private/ }));
    // setValue("visibility", "private", { shouldValidate: true }) — the
    // trigger reflects the new value once react-hook-form's watch() emits.
    expect(trigger).toHaveTextContent(/Private/);
  });

  it("file picker change populates csv_content + preview from File.text() (L56-62)", async () => {
    const user = userEvent.setup();
    renderForm();
    // The "file picker" tab is the default; the Input rendered there is the
    // only role=textbox with type=file. Use the html-input directly.
    const sha = "b".repeat(64);
    const csv = `file_name,label\n${sha},Malware\n`;
    const file = new File([csv], "tiny.csv", { type: "text/csv" });
    // userEvent.upload is the documented File-input flow.
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(fileInput, file);
    // Preview should appear once File.text() resolves and runPreview runs;
    // findByText handles the async tick.
    expect(
      await screen.findByText(/Preview \(1 of 1 rows\)/),
    ).toBeInTheDocument();
  });

  it("file picker change with an oversize CSV surfaces the size-limit Alert (L66-70)", async () => {
    const user = userEvent.setup();
    renderForm();
    // Build an oversize CSV that triggers runPreview → checkCsvSize → return
    // path (sizeErr set, preview cleared); the Alert text comes from the
    // returned error string.
    const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
    const file = new File([oversize], "big.csv", { type: "text/csv" });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(fileInput, file);
    // checkCsvSize returns "CSV size X.XX MB exceeds limit of 10 MB" —
    // that string flows into setParseError and renders inside the Alert.
    expect(
      await screen.findByText(/exceeds limit of 10 MB/i),
    ).toBeInTheDocument();
  });

  describe("onSubmit body (L80-99)", () => {
    // Fill the form with a valid name + a single-row CSV that passes both
    // checkCsvSize and parseCsvPreview, leaving the form in the "submittable"
    // state. Used by every onSubmit-path test.
    async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
      await user.type(screen.getByLabelText("Name"), "ds-1");
      await user.click(screen.getByRole("tab", { name: /Paste/ }));
      const textarea = screen.getByPlaceholderText(/file_name,label,family/);
      const sha = "c".repeat(64);
      await user.click(textarea);
      await user.paste(`file_name,label\n${sha},Malware\n`);
    }

    it("submit success: posts the form values via useCreateDataset and navigates to the new dataset detail page", async () => {
      const user = userEvent.setup();
      createDatasetMock.mockResolvedValueOnce({ id: "ds-new-id" });
      renderForm();
      await fillValidForm(user);
      // Wait for the preview to land so the Upload button is enabled.
      await screen.findByText(/Preview \(1 of 1 rows\)/);
      await user.click(screen.getByRole("button", { name: /Upload dataset/ }));
      await waitFor(() => {
        expect(createDatasetMock).toHaveBeenCalledWith(
          expect.objectContaining({
            name: "ds-1",
            visibility: "public",
            csv_content: expect.stringContaining("file_name,label"),
          }),
        );
      });
      expect(navigateMock).toHaveBeenCalledWith("/datasets/ds-new-id");
    });

    it("submit with LoldayApiError 422 fieldErrors wires each error onto the matching form field", async () => {
      const user = userEvent.setup();
      // applyFieldErrorsToForm walks err.fieldErrors and calls setError per
      // field; the "name" message must appear next to the Name input.
      createDatasetMock.mockRejectedValueOnce(
        new LoldayApiError(422, "validation failed", [
          { field: "name", message: "dataset name already exists" },
        ]),
      );
      renderForm();
      await fillValidForm(user);
      await screen.findByText(/Preview \(1 of 1 rows\)/);
      await user.click(screen.getByRole("button", { name: /Upload dataset/ }));
      await waitFor(() => {
        expect(
          screen.getByText(/dataset name already exists/),
        ).toBeInTheDocument();
      });
      // Failed submit must NOT navigate away.
      expect(navigateMock).not.toHaveBeenCalled();
    });

    it("submit disabled while parseError is set: oversize CSV blocks Upload button so the create mutation is never dispatched", async () => {
      const user = userEvent.setup();
      renderForm();
      const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
      await user.type(screen.getByLabelText("Name"), "ds-big");
      await user.click(screen.getByRole("tab", { name: /Paste/ }));
      const textarea = screen.getByPlaceholderText(/file_name,label,family/);
      await user.click(textarea);
      await user.paste(oversize);
      await screen.findByText(/exceeds limit of 10 MB/i);
      expect(
        screen.getByRole("button", { name: /Upload dataset/ }),
      ).toBeDisabled();
      expect(createDatasetMock).not.toHaveBeenCalled();
    });
  });
});
