/**
 * GET /api/dev/processing-test/:jobId/result[?download=1]
 * → streams processor GET /jobs/{jobId}/result (image/jpeg).
 * With `download=1` the browser saves it as AE_test_{jobId}.jpg.
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
import { isValidDevJobId } from "@/lib/processing/dev-test-types";

export async function GET(
  request: NextRequest,
  ctx: RouteContext<"/api/dev/processing-test/[jobId]/result">,
) {
  await connection(); // runtime env switch – never prerender
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const { jobId } = await ctx.params;
  if (!isValidDevJobId(jobId)) return apiError(400, "invalid_job_id", "Ungültige Auftrags-ID.");

  const call = await callProcessor(guard.value, `/jobs/${encodeURIComponent(jobId)}/result`, {
    method: "GET",
    accept: "image/jpeg",
    timeoutMs: PROCESSOR_TIMEOUTS_MS.file,
    signal: request.signal,
  });
  if (!call.ok) return call.response;
  const upstream = call.value;
  if (!upstream.ok || !upstream.body) {
    return processorErrorResponse(upstream, {
      notFound: "Ergebnis wurde nicht gefunden oder ist abgelaufen.",
      conflict: "Das Ergebnis ist noch nicht fertig.",
    });
  }

  const download = request.nextUrl.searchParams.get("download") === "1";
  const headers = new Headers({
    "Content-Type": "image/jpeg",
    "Cache-Control": "private, no-store",
    "Content-Disposition": download
      ? `attachment; filename="AE_test_${jobId}.jpg"`
      : `inline; filename="AE_test_${jobId}.jpg"`,
  });
  const length = passThroughLength(upstream);
  if (length) headers.set("Content-Length", length);

  return new Response(upstream.body, { status: 200, headers });
}
