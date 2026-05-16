import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/common/StatusBadge";

describe("StatusBadge", () => {
  it("emits data-testid='status-badge-<status>' so RunDetailPage POM can assert i18n-stably", () => {
    const { container } = render(<StatusBadge status="succeeded" />);
    expect(
      container.querySelector('[data-testid="status-badge-succeeded"]'),
    ).not.toBeNull();
  });

  it("emits the matching testid for the failed status", () => {
    const { container } = render(<StatusBadge status="failed" />);
    expect(
      container.querySelector('[data-testid="status-badge-failed"]'),
    ).not.toBeNull();
  });
});
