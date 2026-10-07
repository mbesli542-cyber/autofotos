import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { SupabasePublicConfig } from "./config";

let browserClient: SupabaseClient | null = null;

/** Singleton browser client (session stored in cookies for SSR/route handlers). */
export function getSupabaseBrowserClient(config: SupabasePublicConfig): SupabaseClient {
  browserClient ??= createBrowserClient(config.url, config.anonKey);
  return browserClient;
}
