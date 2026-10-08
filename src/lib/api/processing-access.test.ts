import { describe, expect, it } from "vitest";
import { accessCodesMatch, checkAccessCode, readAccessCodeHeader } from "./processing-access";

describe("processing access code", () => {
  it("needs no code when PROCESSING_ACCESS_CODE is not set", () => {
    expect(checkAccessCode(null, null)).toEqual({ ok: true });
  });

  it("requires the header when a code is configured", () => {
    expect(checkAccessCode("Werkstatt-2026", null)).toEqual({ ok: false, reason: "missing" });
    expect(checkAccessCode("Werkstatt-2026", "  ")).toEqual({ ok: false, reason: "missing" });
    expect(checkAccessCode("Werkstatt-2026", "werkstatt-2026")).toEqual({ ok: false, reason: "invalid" });
    expect(checkAccessCode("Werkstatt-2026", "Werkstatt-2026")).toEqual({ ok: true });
  });

  it("accepts URI-encoded codes (non-ASCII characters)", () => {
    expect(checkAccessCode("Schlüssel ä", encodeURIComponent("Schlüssel ä"))).toEqual({ ok: true });
    expect(readAccessCodeHeader("%E0%A4%A")).toBeNull();
  });

  it("compares codes of different lengths safely", () => {
    expect(accessCodesMatch("abc", "abcd")).toBe(false);
    expect(accessCodesMatch("abc", "abc")).toBe(true);
  });
});
