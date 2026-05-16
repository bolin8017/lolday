import { describe, expect, it } from "vitest";

import { DetectorPage } from "@/../tests/e2e/helpers/detector.po";
import { ModelPage } from "@/../tests/e2e/helpers/model.po";

describe("DetectorPage POM", () => {
  it("exposes navigation + build methods", () => {
    const fakePage = {} as never;
    const pom = new DetectorPage(fakePage);
    expect(typeof pom.gotoList).toBe("function");
    expect(typeof pom.gotoDetail).toBe("function");
    expect(typeof pom.gotoNew).toBe("function");
    expect(typeof pom.triggerBuild).toBe("function");
    expect(typeof pom.versionRow).toBe("function");
  });
});

describe("ModelPage POM", () => {
  it("exposes navigation + transfer + delete methods", () => {
    const fakePage = {} as never;
    const pom = new ModelPage(fakePage);
    expect(typeof pom.gotoList).toBe("function");
    expect(typeof pom.gotoDetail).toBe("function");
    expect(typeof pom.transferTo).toBe("function");
    expect(typeof pom.deleteModel).toBe("function");
    expect(typeof pom.row).toBe("function");
  });
});
