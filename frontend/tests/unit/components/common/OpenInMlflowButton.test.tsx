import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";

describe("OpenInMlflowButton", () => {
  it("links to /mlflow/ when neither experimentId nor runId is supplied", () => {
    render(<OpenInMlflowButton />);
    const anchor = screen.getByRole("link", { name: /MLflow/i });
    expect(anchor).toHaveAttribute("href", "/mlflow/");
  });

  it("links to the experiment page when only experimentId is supplied", () => {
    render(<OpenInMlflowButton experimentId="42" />);
    expect(screen.getByRole("link", { name: /MLflow/i })).toHaveAttribute(
      "href",
      "/mlflow/#/experiments/42",
    );
  });

  it("links to the run page when both experimentId and runId are supplied", () => {
    render(<OpenInMlflowButton experimentId="42" runId="abc123" />);
    expect(screen.getByRole("link", { name: /MLflow/i })).toHaveAttribute(
      "href",
      "/mlflow/#/experiments/42/runs/abc123",
    );
  });

  it("falls back to /mlflow/ when runId is supplied without experimentId", () => {
    // Defensive guard — Radix-y "either both or only experiment, never just
    // run" callers exist, but pin the helper's behaviour here.
    render(<OpenInMlflowButton runId="orphan" />);
    expect(screen.getByRole("link", { name: /MLflow/i })).toHaveAttribute(
      "href",
      "/mlflow/",
    );
  });

  it("opens in a new tab with rel='noopener noreferrer' (no window.opener leak)", () => {
    // Security contract — MLflow is a different origin under our reverse
    // proxy, so the noopener gate is what prevents the destination page
    // from controlling the opener via window.opener.
    render(<OpenInMlflowButton experimentId="42" />);
    const anchor = screen.getByRole("link", { name: /MLflow/i });
    expect(anchor).toHaveAttribute("target", "_blank");
    expect(anchor).toHaveAttribute("rel", "noopener noreferrer");
  });
});
