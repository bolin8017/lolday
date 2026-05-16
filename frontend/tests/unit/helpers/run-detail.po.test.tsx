import { describe, expect, it } from "vitest";

import { RunDetailPage } from "@/../tests/e2e/helpers/run-detail.po";

describe("RunDetailPage POM", () => {
  it("exposes navigation + mlflow link methods", () => {
    const fakePage = {} as never;
    const pom = new RunDetailPage(fakePage);
    expect(typeof pom.goto).toBe("function");
    expect(typeof pom.openInMlflow).toBe("function");
    expect(typeof pom.metricRow).toBe("function");
    expect(typeof pom.expectStatus).toBe("function");
  });
});
