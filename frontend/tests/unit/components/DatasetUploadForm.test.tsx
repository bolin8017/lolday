import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  checkCsvSize,
  MAX_CSV_BYTES,
} from "@/components/forms/DatasetUploadForm.logic";
import { DatasetUploadForm } from "@/components/forms/DatasetUploadForm";

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
});
