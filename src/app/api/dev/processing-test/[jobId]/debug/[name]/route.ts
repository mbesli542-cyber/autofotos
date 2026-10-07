/**
 * GET /api/dev/processing-test/:jobId/debug/:name
 * → streams processor GET /jobs/{jobId}/debug/{name}
 * (files exist only while the processor runs with PROCESSOR_DEBUG=true).
 */
import { connection, type NextRequest } from "next/server";
import {
  PROCESSOR_TIMEOUTS_MS,
  apiError,
  callProcessor,
  guardDevProcessorRoute,
  passThroughLength,
  processorErrorResponse,
} from "@/lib/dev-tools";
import { isValidDebugFileName, isValidDevJobId } from "@/lib/processing/dev-test-types";

/** Content types we pass through; anything else is served as a download. */
const CONTENT_TYPES: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  json: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8",
};

export async function GET(
  request: NextRequest,
  ctx: RouteContext<"/api/dev/processing-test/[jobId]/debug/[name]">,
) {
  await connection(); // runtime env switch – never prerender
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const { jobId, name } = await ctx.params;
  if (!isValidDevJobId(jobId)) return apiError(400, "invalid_job_id", "Ungültige Auftrags-ID.");
  if (!isValidDebugFileName(name)) return apiError(400, "invalid_name", "Ungültiger Dateiname.");

  const call = await callProcessor(
    guard.value,
    `/jobs/${encodeURIComponent(jobId)}/debug/${encodeURIComponent(name)}`,
    {
      method: "GET",
      accept: "*/*",
      timeoutMs: PROCESSOR_TIMEOUTS_MS.file,
      signal: request.signal,
    },
  );
  if (!call.ok) return call.response;
  const upstream = call.value;
  if (!upstream.ok || !upstream.body) {
    return processorErrorResponse(upstream, {
      notFound:
        "Debug-Datei wurde nicht gefunden. Debug-Dateien gibt es nur mit PROCESSOR_DEBUG=true.",
    });
  }

  const extension = name.split(".").pop() ?? "";
  const contentType = CONTENT_TYPES[extension];
  const headers = new Headers({
    "Content-Type": contentType ?? "application/octet-stream",
    "Cache-Control": "private, no-store",
    "Content-Disposition": `${contentType ? "inline" : "attachment"}; filename="${name}"`,
  });
  const length = passThroughLength(upstream);
  if (length) headers.set("Content-Length", length);

  return new Response(upstream.body, { status: 200, headers });
}
