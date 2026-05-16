/**
 * D3.5 — i18n drift contract: zh-TW ⊇ en.
 *
 * lolday's source-of-truth language is zh-TW; en is the secondary.
 * A missing zh-TW key falls back to the literal English string,
 * surfacing as a Chinese-UI English-leak. This test enforces the
 * superset relation so any drift fails CI.
 *
 * (The symmetric en ⊇ zh-TW direction is NOT enforced — zh-TW may
 * carry Taiwanese-only keys without an English counterpart.)
 */
import { describe, expect, it } from "vitest";

import en from "@/i18n/en.json";
import zhTW from "@/i18n/zh-TW.json";

type Json = string | number | boolean | null | { [k: string]: Json } | Json[];

function paths(obj: Json, prefix = ""): string[] {
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return prefix ? [prefix] : [];
  }
  return Object.entries(obj).flatMap(([k, v]) =>
    paths(v as Json, prefix ? `${prefix}.${k}` : k),
  );
}

describe("i18n key drift", () => {
  const enPaths = new Set(paths(en as Json));
  const zhPaths = new Set(paths(zhTW as Json));

  it("every en.json key exists in zh-TW.json", () => {
    const missing = [...enPaths].filter((p) => !zhPaths.has(p));
    expect(
      missing,
      `zh-TW.json missing ${missing.length} keys: ${missing.slice(0, 10).join(", ")}`,
    ).toEqual([]);
  });
});
