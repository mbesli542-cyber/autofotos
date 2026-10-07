/**
 * GET /api/dev/processing-test/:jobId → processor GET /jobs/{jobId}
 * (developer test page only; 404 when dev tools are disabled).
 */
import { NextResponse, connection, type NextRequest } from "next/server";
import {
  PROCESSOR_TIMEOUTS_MS,
  apiError,
  callProcessor,
  guardDevProcessorRoute,
  invalidProcessorResponse,
  processorErrorResponse,
  readJson,
} from "@/lib/dev-tools";
import {
  isValidDevJobId,
  parseProcessorJob,
  type ProcessorJob,
} from "@/lib/processing/dev-test-types";

export async function GET(request: NextRequest, ctx: RouteContext<"/api/dev/processing-test/[jobId]">) {
  await connection(); // runtime env switch – never prerender
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const { jobId } = await ctx.params;
  if (!isValidDevJobId(jobId)) return apiError(400, "invalid_job_id", "Ungültige Auftrags-ID.");

  const call = await callProcessor(guard.value, `/jobs/${encodeURIComponent(jobId)}`, {
    method: "GET",
    accept: "application/json",
    timeoutMs: PROCESSOR_TIMEOUTS_MS.job,
    signal: request.signal,
  });
  if (!call.ok) return call.response;
  if (!call.value.ok) {
    return processorErrorResponse(call.value, {
      notFound: "Auftrag wurde nicht gefunden oder ist abgelaufen.",
    });
  }

  const job = parseProcessorJob(await readJson(call.value));
  if (!job || job.jobId !== jobId) return invalidProcessorResponse();
  return NextResponse.json<ProcessorJob>(job, { headers: { "Cache-Control": "no-store" } });
}
