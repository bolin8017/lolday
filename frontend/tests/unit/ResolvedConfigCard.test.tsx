import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ResolvedConfigCard } from "@/components/jobs/ResolvedConfigCard";

describe("ResolvedConfigCard", () => {
  const resolvedConfig = {
    paths: { train: "/x" },
    params: { n_estimators: 200 },
  };

  it("shows user params table when userParams provided", () => {
    render(
      <ResolvedConfigCard
        resolvedConfig={resolvedConfig}
        userParams={{ n_estimators: 200 }}
        detectorDefaults={{ n_estimators: 100 }}
      />,
    );
    expect(screen.getByText("n_estimators")).toBeInTheDocument();
    expect(screen.getByText(/200/)).toBeInTheDocument();
  });

  it("shows legacy fallback when userParams is null", () => {
    render(
      <ResolvedConfigCard resolvedConfig={resolvedConfig} userParams={null} />,
    );
    expect(screen.getByText(/legacy job/i)).toBeInTheDocument();
  });

  it("toggles full resolved config visibility", () => {
    render(
      <ResolvedConfigCard resolvedConfig={resolvedConfig} userParams={{}} />,
    );
    const toggle = screen.getByRole("button", { name: /show full/i });
    expect(toggle).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(
      screen.getByRole("button", { name: /hide full/i }),
    ).toBeInTheDocument();
  });
});
