/**
 * Developer tools (SERVER ONLY) – e.g. the test page `/dev/processing-test`.
 *
 * - Always available in `next dev`.
 * - In production builds only with ENABLE_DEV_TOOLS=true (runtime switch).
 *   The dev routes are NOT behind the login – enable them only on internal
 *   or staging deployments.
 *
 * Reads non-public environment variables: never import this from client
 * components. Read it at request time (after `connection()` in pages and
 * GET route handlers) so the switch is not frozen into a prerender.
 * src/proxy.ts uses isDevToolsEnabled() to answer /dev/* with a real 404.
 */
import { NextResponse } from "next/server";
import { parseProcessorConnection, type ProcessorConnection } from "@/lib/processing/processor-config";
import { joinServiceUrl } from "@/lib/processing/real-image-processor";
import type { ApiErrorBody } from "@/lib/processing/types";

export function isDevToolsEnabled(): boolean {
  return process.env.NODE_ENV !== "production" || process.env.ENABLE_DEV_TOOLS === "true";
}

/** Same shape as `apiError` (src/lib/api/route-helpers) – kept local so the proxy can import this module. */
export function apiError(status: number, code: string, message: string) {
  return NextResponse.json<ApiErrorBody>({ error: { code, message } }, { status });
}

export type ProcessorConfig = ProcessorConnection;

/**
 * Processor connection from the environment, or null if not (validly)
 * configured. The test page only needs the URL (IMAGE_PROCESSOR is not
 * required – it never stores results in the app).
 */
export function getProcessorConfig(): ProcessorConfig | null {
  const connection = parseProcessorConnection({
    IMAGE_PROCESSING_API_URL: process.env.IMAGE_PROCESSING_API_URL,
    IMAGE_PROCESSING_API_KEY: process.env.IMAGE_PROCESSING_API_KEY,
  });
  return "error" in connection ? null : connection;
}

/* ------------------------------------------------------------------------ */
/* Helpers for the /api/dev/processing-test proxy routes                     */
/* ------------------------------------------------------------------------ */

export const PROCESSOR_NOT_CONFIGURED_MESSAGE =
  "Bildverarbeitungs-Service ist nicht konfiguriert (IMAGE_PROCESSING_API_URL).";

type Guarded<T> = { ok: true; value: T } | { ok: false; response: NextResponse };

/** 404 when dev tools are off, 503 when the processor is not configured. */
export function guardDevProcessorRoute(): Guarded<ProcessorConfig> {
  if (!isDevToolsEnabled()) {
    return { ok: false, response: apiError(404, "not_found", "Nicht gefunden.") };
  }
  const config = getProcessorConfig();
  if (!config) {
    return {
      ok: false,
      response: apiError(503, "processor_not_configured", PROCESSOR_NOT_CONFIGURED_MESSAGE),
    };
  }
  return { ok: true, value: config };
}

export const PROCESSOR_TIMEOUTS_MS = {
  health: 5_000,
  upload: 60_000,
  job: 15_000,
  file: 30_000,
} as const;

/**
 * Calls the processor. `path` and `query` must be built from validated values
 * only (`query` is appended as URL search parameters).
 * The timeout covers the time until the response headers arrive; the body is
 * then streamed and cancelled when the browser disconnects (`signal`).
 * Network failures → 502, timeouts → 504 (German messages).
 */
export async function callProcessor(
  config: ProcessorConfig,
  path: string,
  init: {
    method: "GET" | "POST";
    body?: FormData;
    query?: Record<string, string>;
    accept: string;
    timeoutMs: number;
    signal?: AbortSignal;
  },
): Promise<Guarded<Response>> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), init.timeoutMs);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout.signal]) : timeout.signal;
  try {
    const url = joinServiceUrl(config.baseUrl, path);
    for (const [key, value] of Object.entries(init.query ?? {})) url.searchParams.set(key, value);
    const response = await fetch(url, {
      method: init.method,
      body: init.body,
      signal,
      cache: "no-store",
      // Never follow redirects to other hosts/paths with our credentials.
      redirect: "manual",
      headers: {
        Accept: init.accept,
        ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
      },
    });
    return { ok: true, value: response };
  } catch {
    if (timeout.signal.aborted) {
      return {
        ok: false,
        response: apiError(
          504,
          "processor_timeout",
          "Der Bildverarbeitungs-Service antwortet nicht (Zeitüberschreitung). Bitte versuchen Sie es erneut.",
        ),
      };
    }
    return {
      ok: false,
      response: apiError(
        502,
        "processor_unreachable",
        "Der Bildverarbeitungs-Service ist nicht erreichbar. Bitte prüfen Sie, ob der Dienst läuft.",
      ),
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Code and German message from a processor error body ({ error: { code, message } }). */
async function readProcessorError(response: Response): Promise<{ code: string | null; message: string | null }> {
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } } | null;
    const code = body?.error?.code;
    const message = body?.error?.message;
    return {
      code: typeof code === "string" && /^[a-z_]{1,40}$/.test(code) ? code : null,
      message: typeof message === "string" && message.length > 0 && message.length <= 300 ? message : null,
    };
  } catch {
    return { code: null, message: null };
  }
}

/** German message from a processor error body ({ error: { message } }), if usable. */
async function readProcessorMessage(response: Response): Promise<string | null> {
  return (await readProcessorError(response)).message;
}

/**
 * Maps a non-OK processor response to a German API error. Validation
 * messages of the processor (its own `{ error }` format) are passed through.
 */
export async function processorErrorResponse(
  response: Response,
  messages: { notFound: string; conflict?: string },
): Promise<NextResponse> {
  const result = await mapProcessorError(response, messages);
  // Release the upstream connection if the body was not consumed.
  if (!response.bodyUsed) await response.body?.cancel().catch(() => undefined);
  return result;
}

async function mapProcessorError(
  response: Response,
  messages: { notFound: string; conflict?: string },
): Promise<NextResponse> {
  const { status } = response;
  if (status === 401 || status === 403) {
    return apiError(
      502,
      "processor_auth",
      "Der Bildverarbeitungs-Service hat die Anfrage abgelehnt. Bitte IMAGE_PROCESSING_API_KEY prüfen.",
    );
  }
  if (status === 404) return apiError(404, "not_found", messages.notFound);
  if (status === 409) {
    return apiError(409, "not_ready", messages.conflict ?? "Das Ergebnis ist noch nicht fertig.");
  }
  if (status === 413) {
    return apiError(
      413,
      "file_too_large",
      (await readProcessorMessage(response)) ?? "Das Foto ist zu groß für den Bildverarbeitungs-Service.",
    );
  }
  if (status === 400 || status === 415 || status === 422) {
    return apiError(
      400,
      "invalid_request",
      (await readProcessorMessage(response)) ??
        "Das Foto konnte nicht verarbeitet werden. Bitte prüfen Sie das Dateiformat.",
    );
  }
  if (status === 429 || status === 503) {
    // 503 is also used for configuration problems (e.g. a broken showroom
    // preset) – keep the processor's own German message and code then.
    const { code, message } = await readProcessorError(response);
    if (status === 503 && code === "configuration" && message) {
      return apiError(503, "processor_configuration", message);
    }
    return apiError(
      503,
      "processor_busy",
      message ?? "Der Bildverarbeitungs-Service ist ausgelastet. Bitte versuchen Sie es gleich erneut.",
    );
  }
  return apiError(
    502,
    "processor_error",
    "Der Bildverarbeitungs-Service hat einen Fehler gemeldet. Bitte versuchen Sie es erneut.",
  );
}

export function invalidProcessorResponse(): NextResponse {
  return apiError(
    502,
    "processor_invalid_response",
    "Ungültige Antwort vom Bildverarbeitungs-Service.",
  );
}

/** Content-Length of an upstream body we pass through unchanged (not if re-encoded). */
export function passThroughLength(upstream: Response): string | null {
  if (upstream.headers.has("content-encoding")) return null;
  const length = upstream.headers.get("content-length");
  return length && /^\d+$/.test(length) ? length : null;
}

/** Reads a JSON body; null if it is not valid JSON. */
export async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}
