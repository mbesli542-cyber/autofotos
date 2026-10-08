import { describe, expect, it } from "vitest";
import { resolveBackendMode } from "./backend-mode";

describe("resolveBackendMode", () => {
  it("defaults to supabase when configured, otherwise demo", () => {
    expect(resolveBackendMode(undefined, true)).toEqual({ mode: "supabase", warning: null });
    expect(resolveBackendMode(undefined, false)).toEqual({ mode: "demo", warning: null });
    expect(resolveBackendMode("", true)).toEqual({ mode: "supabase", warning: null });
  });

  it("forces demo mode even when Supabase is configured", () => {
    expect(resolveBackendMode("demo", true)).toEqual({ mode: "demo", warning: null });
    expect(resolveBackendMode(" Demo ", true).mode).toBe("demo");
  });

  it("uses supabase only when its env is complete – otherwise demo with a warning", () => {
    expect(resolveBackendMode("supabase", true)).toEqual({ mode: "supabase", warning: null });
    const fallback = resolveBackendMode("supabase", false);
    expect(fallback.mode).toBe("demo");
    expect(fallback.warning).toMatch(/demo mode/);
  });

  it("warns about unknown values and keeps the default", () => {
    const decision = resolveBackendMode("firebase", false);
    expect(decision.mode).toBe("demo");
    expect(decision.warning).toMatch(/Unknown NEXT_PUBLIC_DATA_BACKEND/);
    expect(resolveBackendMode("firebase", true).mode).toBe("supabase");
  });
});
