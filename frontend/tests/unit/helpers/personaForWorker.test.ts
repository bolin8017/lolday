import { describe, expect, it } from "vitest";

import { personaForWorker } from "@/../tests/e2e/helpers/auth";

describe("personaForWorker", () => {
  it("returns admin for worker 0", () => {
    expect(personaForWorker(0)).toBe("admin");
  });

  it("returns developer for worker 1", () => {
    expect(personaForWorker(1)).toBe("developer");
  });

  it("returns user for worker 2", () => {
    expect(personaForWorker(2)).toBe("user");
  });

  it("cycles by mod-3 — worker 3 reuses admin", () => {
    expect(personaForWorker(3)).toBe("admin");
    expect(personaForWorker(6)).toBe("admin");
  });

  it("rejects negative or non-integer indices", () => {
    expect(() => personaForWorker(-1)).toThrow();
    expect(() => personaForWorker(1.5)).toThrow();
  });
});
