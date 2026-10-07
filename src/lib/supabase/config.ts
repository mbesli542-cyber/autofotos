/**
 * Public Supabase configuration.
 *
 * If these variables are missing the app runs in demo mode (local mock data).
 * Only the public URL and anon/publishable key are ever used in the browser –
 * never the service role key.
 */
export interface SupabasePublicConfig {
  url: string;
  anonKey: string;
}

export function getSupabasePublicConfig(): SupabasePublicConfig | null {
  // Must be referenced literally so Next.js can inline them at build time.
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey =
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !anonKey) return null;
  return { url, anonKey };
}

export function isSupabaseConfigured(): boolean {
  return getSupabasePublicConfig() !== null;
}
