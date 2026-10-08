/**
 * Which data backend the app uses – decided independently of the image
 * processor (see src/lib/processing/processor-config.ts).
 *
 *   NEXT_PUBLIC_DATA_BACKEND=demo      → demo mode (IndexedDB), even if Supabase is configured
 *   NEXT_PUBLIC_DATA_BACKEND=supabase  → Supabase; without Supabase env: demo + console warning
 *   (unset)                            → Supabase if configured, otherwise demo
 *
 * Read by the browser (getBackendMode, createBackend) and the server
 * (authenticateRequest, proxy), so both sides always agree.
 */
import type { BackendMode } from "@/lib/data/types";
import { isSupabaseConfigured } from "@/lib/supabase/config";

export interface BackendModeDecision {
  mode: BackendMode;
  /** Developer warning (console only) when the setting cannot be honoured. */
  warning: string | null;
}

/** Pure decision from the raw setting and whether the Supabase env is complete. */
export function resolveBackendMode(
  setting: string | undefined,
  supabaseConfigured: boolean,
): BackendModeDecision {
  const value = setting?.trim().toLowerCase() ?? "";
  if (value === "demo") return { mode: "demo", warning: null };
  if (value === "supabase") {
    return supabaseConfigured
      ? { mode: "supabase", warning: null }
      : {
          mode: "demo",
          warning:
            "NEXT_PUBLIC_DATA_BACKEND=supabase, but NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are missing – running in demo mode.",
        };
  }
  const mode: BackendMode = supabaseConfigured ? "supabase" : "demo";
  if (value === "") return { mode, warning: null };
  return {
    mode,
    warning: `Unknown NEXT_PUBLIC_DATA_BACKEND="${setting}" (allowed: demo, supabase) – using ${mode}.`,
  };
}

let warned = false;

/** The active data backend (browser and server). Safe during render. */
export function getDataBackendMode(): BackendMode {
  // Referenced literally so Next.js can inline it at build time.
  const decision = resolveBackendMode(process.env.NEXT_PUBLIC_DATA_BACKEND, isSupabaseConfigured());
  if (decision.warning && !warned) {
    warned = true;
    console.warn(`[AutoExperten Photo] ${decision.warning}`);
  }
  return decision.mode;
}
