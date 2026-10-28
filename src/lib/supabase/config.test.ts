import { afterEach, describe, expect, it, vi } from "vitest";
import { getSupabasePublicConfig, isSupabaseConfigured } from "./config";

const URL_VALUE = "https://example.supabase.co";

function setEnv(env: { url?: string; anon?: string; publishable?: string }) {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", env.url);
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", env.anon);
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", env.publishable);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("getSupabasePublicConfig", () => {
  it("uses the publishable key when the anon key line is present but empty (.env.example)", () => {
    setEnv({ url: URL_VALUE, anon: "", publishable: "sb_publishable_TESTKEY" });
    expect(getSupabasePublicConfig()).toEqual({ url: URL_VALUE, anonKey: "sb_publishable_TESTKEY" });
    expect(isSupabaseConfigured()).toBe(true);
  });

  it("prefers the (legacy) anon key when both are set", () => {
    setEnv({ url: URL_VALUE, anon: "anon-key", publishable: "sb_publishable_TESTKEY" });
    expect(getSupabasePublicConfig()?.anonKey).toBe("anon-key");
  });

  it("uses the publishable key alone", () => {
    setEnv({ url: URL_VALUE, publishable: "sb_publishable_TESTKEY" });
    expect(getSupabasePublicConfig()?.anonKey).toBe("sb_publishable_TESTKEY");
  });

  it("is not configured without a URL or without any key (demo mode)", () => {
    setEnv({ url: URL_VALUE, anon: "", publishable: "" });
    expect(getSupabasePublicConfig()).toBeNull();
    setEnv({ url: "", anon: "anon-key" });
    expect(isSupabaseConfigured()).toBe(false);
  });
});
