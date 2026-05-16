/**
 * D3.1 — JobSubmitPage POM is a thin selector layer; its behavioural test
 * is performed in the E2E suite. This unit test only asserts the POM's
 * shape: it exposes the documented methods + chains play nicely with
 * playwright's typed `Page` instance.
 */
import { describe, expect, it } from "vitest";

import { JobSubmitPage } from "@/../tests/e2e/helpers/job-submit.po";

describe("JobSubmitPage POM", () => {
  it("constructor stores page", () => {
    const fakePage = { goto: () => Promise.resolve() } as never;
    const pom = new JobSubmitPage(fakePage);
    expect(pom).toBeInstanceOf(JobSubmitPage);
  });

  it("exposes the documented selectors as methods", () => {
    const fakePage = {} as never;
    const pom = new JobSubmitPage(fakePage);
    expect(typeof pom.goto).toBe("function");
    expect(typeof pom.selectJobType).toBe("function");
    expect(typeof pom.pickDetector).toBe("function");
    expect(typeof pom.pickVersion).toBe("function");
    expect(typeof pom.pickTrainDataset).toBe("function");
    expect(typeof pom.pickTestDataset).toBe("function");
    expect(typeof pom.submit).toBe("function");
    expect(typeof pom.submitButton).toBe("function");
  });
});
