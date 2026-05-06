import { describe, it, expect } from "vitest";
import { aggregateLongTail } from "@/components/charts/FamilyDistribution.logic";

describe("aggregateLongTail", () => {
  it("returns empty array on empty input", () => {
    expect(aggregateLongTail({})).toEqual([]);
  });

  it("returns sorted entries when count <= topN", () => {
    const result = aggregateLongTail({ a: 1, b: 3, c: 2 }, 10);
    expect(result).toEqual([
      { name: "b", value: 3 },
      { name: "c", value: 2 },
      { name: "a", value: 1 },
    ]);
    expect(result.some((b) => b.isOther)).toBe(false);
  });

  it("returns exactly topN when input length equals topN", () => {
    const data = Object.fromEntries(
      Array.from({ length: 10 }, (_, i) => [`f${i}`, 10 - i]),
    );
    const out = aggregateLongTail(data, 10);
    expect(out).toHaveLength(10);
    expect(out.some((b) => b.isOther)).toBe(false);
  });

  it("aggregates the long tail into Other(N) when count > topN", () => {
    const data = Object.fromEntries(
      Array.from({ length: 12 }, (_, i) => [`f${i}`, 12 - i]),
    );
    const out = aggregateLongTail(data, 10);
    expect(out).toHaveLength(11);
    const last = out[out.length - 1];
    expect(last.isOther).toBe(true);
    expect(last.name).toBe("Other (2)");
    expect(last.value).toBe(1 + 2); // f10=2, f11=1
  });

  it("sorts top entries descending by value, ties broken by insertion order", () => {
    const out = aggregateLongTail({ z: 5, a: 5, m: 1 }, 10);
    expect(out.map((b) => b.name)).toEqual(["z", "a", "m"]);
  });

  it("does not mutate the input object", () => {
    const data = { a: 1, b: 2 };
    const snapshot = { ...data };
    aggregateLongTail(data, 10);
    expect(data).toEqual(snapshot);
  });
});
