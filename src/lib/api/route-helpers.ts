/** Shared helpers for route handlers (server only). */
import type { SupabaseClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import type { ApiErrorBody } from "@/lib/processing/types";
import { createSupabaseServerClient } from "@/lib/supabase/server-client";

export function apiError(status: number, code: string, message: string) {
  return NextResponse.json<ApiErrorBody>({ error: { code, message } }, { status });
}

export type RouteAuth =
  | { ok: true; mode: "demo"; userId: null; client: null }
  | { ok: true; mode: "supabase"; userId: string; client: SupabaseClient }
  | { ok: false; response: NextResponse };

/**
 * In Supabase mode the request must carry a valid session.
 * In demo mode (no Supabase configured) data lives in the browser, so the
 * mock API accepts requests without authentication.
 */
export async function authenticateRequest(): Promise<RouteAuth> {
  const client = await createSupabaseServerClient();
  if (!client) return { ok: true, mode: "demo", userId: null, client: null };

  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    return {
      ok: false,
      response: apiError(401, "unauthorized", "Bitte melden Sie sich erneut an."),
    };
  }
  return { ok: true, mode: "supabase", userId: data.user.id, client };
}
