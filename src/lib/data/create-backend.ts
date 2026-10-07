/**
 * Chooses the backend: Supabase when configured, otherwise demo mode.
 * Constructors do not touch browser APIs, so this is safe during SSR.
 */
import { getSupabaseBrowserClient } from "@/lib/supabase/browser-client";
import { getSupabasePublicConfig } from "@/lib/supabase/config";
import { DemoAuthService } from "./mock/demo-auth-service";
import { MockDataProvider } from "./mock/mock-data-provider";
import { SupabaseAuthService } from "./supabase/supabase-auth-service";
import { SupabaseDataProvider } from "./supabase/supabase-data-provider";
import type { Backend } from "./types";

export function createBackend(): Backend {
  const config = getSupabasePublicConfig();
  if (config) {
    const client = getSupabaseBrowserClient(config);
    return {
      mode: "supabase",
      auth: new SupabaseAuthService(client),
      data: new SupabaseDataProvider(client),
    };
  }
  return {
    mode: "demo",
    auth: new DemoAuthService(),
    data: new MockDataProvider(),
  };
}
