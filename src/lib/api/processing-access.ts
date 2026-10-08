/**
 * Access rules for the processing routes (server only).
 *
 * - Supabase mode: the normal login (session cookie, RLS).
 * - Demo mode: there is no login. On a public demo deployment the optional
 *   server env PROCESSING_ACCESS_CODE protects the processor from abuse:
 *   requests must then send the header `x-processing-access-code` (the code,
 *   URI-encoded) – compared in constant time. Without the env var every
 *   visitor of the deployment can use the processor.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";
import {
  ACCESS_CODE_REQUIRED_CODE,
  PROCESSING_ACCESS_CODE_HEADER,
} from "@/lib/processing/types";
import { apiError, authenticateRequest, type RouteAuth } from "./route-helpers";

export const ACCESS_CODE_MISSING_MESSAGE = "Bitte geben Sie den Zugangscode für die Bildbearbeitung ein.";
export const ACCESS_CODE_INVALID_MESSAGE = "Der Zugangscode für die Bildbearbeitung ist ungültig.";

/** PROCESSING_ACCESS_CODE (trimmed), or null if not set. Read at request time. */
export function getProcessingAccessCode(): string | null {
  const code = process.env.PROCESSING_ACCESS_CODE?.trim();
  return code ? code : null;
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

/** Constant-time comparison (hashing first hides the length of the code). */
export function accessCodesMatch(expected: string, provided: string): boolean {
  return timingSafeEqual(digest(expected), digest(provided));
}

/** Decodes the header value; null if missing or malformed. */
export function readAccessCodeHeader(value: string | null): string | null {
  if (value === null) return null;
  try {
    const decoded = decodeURIComponent(value).trim();
    return decoded ? decoded : null;
  } catch {
    return null;
  }
}

export type AccessCheck = { ok: true } | { ok: false; reason: "missing" | "invalid" };

/** Pure check of a request header against the configured code. */
export function checkAccessCode(expected: string | null, headerValue: string | null): AccessCheck {
  if (!expected) return { ok: true };
  const provided = readAccessCodeHeader(headerValue);
  if (!provided) return { ok: false, reason: "missing" };
  return accessCodesMatch(expected, provided) ? { ok: true } : { ok: false, reason: "invalid" };
}

/**
 * Authentication for /api/process-upload and /api/process-job/*:
 * Supabase session in Supabase mode, access code (if configured) in demo mode.
 */
export async function authorizeProcessingRequest(request: NextRequest): Promise<RouteAuth> {
  const auth = await authenticateRequest();
  if (!auth.ok || auth.mode === "supabase") return auth;
  const check = checkAccessCode(getProcessingAccessCode(), request.headers.get(PROCESSING_ACCESS_CODE_HEADER));
  if (check.ok) return auth;
  return {
    ok: false,
    response: apiError(
      401,
      ACCESS_CODE_REQUIRED_CODE,
      check.reason === "missing" ? ACCESS_CODE_MISSING_MESSAGE : ACCESS_CODE_INVALID_MESSAGE,
    ),
  };
}
