/**
 * GET /api/process-job/:jobId  (both data backends)
 * → 200 ProcessingJob { jobId, status: queued|processing|complete|failed, progress, result, error, … }
 *
 * `result` is { kind: "stored", processedStoragePath } (Supabase contract) or
 * { kind: "file", width, height, bytes } (upload jobs – download the JPEG via
 * ./result). The processor's own result URL is never passed on.
 * Demo mode: requires the access code if PROCESSING_ACCESS_CODE is set.
 */
import { NextResponse, type NextRequest } from "next/server";
import { authorizeProcessingRequest } from "@/lib/api/processing-access";
import { processingErrorResponse, processingNotConnected } from "@/lib/api/processing-errors";
import { apiError } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { isValidProcessingJobId, type ProcessingJob } from "@/lib/processing/types";

export async function GET(request: NextRequest, ctx: RouteContext<"/api/process-job/[jobId]">) {
  const { jobId } = await ctx.params;
  if (!isValidProcessingJobId(jobId)) {
    return apiError(400, "invalid_job_id", "Ungültige Auftrags-ID.");
  }

  const auth = await authorizeProcessingRequest(request);
  if (!auth.ok) return auth.response;

  const processor = getImageProcessor();
  if (!processor) return processingNotConnected();

  try {
    const job = await processor.getJob(jobId);
    if (!job) return apiError(404, "not_found", "Auftrag wurde nicht gefunden oder ist abgelaufen.");
    return NextResponse.json<ProcessingJob>(job, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return processingErrorResponse(error);
  }
}
