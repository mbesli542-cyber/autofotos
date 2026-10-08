/** Shared helpers for route handlers (server only). */
import type { SupabaseClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { getDataBackendMode } from "@/lib/data/backend-mode";
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
 * In demo mode (NEXT_PUBLIC_DATA_BACKEND=demo or no Supabase env) data lives
 * in the browser and there is no login – processing routes then use the
 * optional access code instead (see ./processing-access).
 */
export async function authenticateRequest(): Promise<RouteAuth> {
  const demo: RouteAuth = { ok: true, mode: "demo", userId: null, client: null };
  if (getDataBackendMode() !== "supabase") return demo;
  const client = await createSupabaseServerClient();
  if (!client) return demo;

  const { data, error } = await client.auth.getUser();
  if (error || !data.user) {
    return {
      ok: false,
      response: apiError(401, "unauthorized", "Bitte melden Sie sich erneut an."),
    };
  }
  return { ok: true, mode: "supabase", userId: data.user.id, client };
}
