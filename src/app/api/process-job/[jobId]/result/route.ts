/**
 * GET /api/process-job/:jobId/result  (upload jobs, demo mode)
 * → 200 image/jpeg – the processed photo, streamed from the processor
 *   (GET {processor}/jobs/{jobId}/result); 409 while it is not ready yet.
 *
 * Only results of jobs that really completed are passed on (never a
 * fallback-showroom composite – see processor-contract.ts).
 * Demo mode: requires the access code if PROCESSING_ACCESS_CODE is set.
 */
import type { NextRequest } from "next/server";
import { authorizeProcessingRequest } from "@/lib/api/processing-access";
import { processingErrorResponse, processingNotConnected } from "@/lib/api/processing-errors";
import { apiError } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { isValidProcessingJobId } from "@/lib/processing/types";

const NOT_FOUND_MESSAGE = "Ergebnis wurde nicht gefunden oder ist abgelaufen.";
const NOT_READY_MESSAGE = "Das Ergebnis ist noch nicht fertig.";

export async function GET(request: NextRequest, ctx: RouteContext<"/api/process-job/[jobId]/result">) {
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
    if (!job) return apiError(404, "not_found", NOT_FOUND_MESSAGE);
    if (job.status === "failed") {
      return apiError(422, "processing_failed", job.error ?? "Bearbeitung fehlgeschlagen.");
    }
    if (job.status !== "complete" || job.result?.kind !== "file") {
      return apiError(409, "not_ready", NOT_READY_MESSAGE);
    }

    const file = await processor.fetchResult(jobId, request.signal);
    if (!file) return apiError(404, "not_found", NOT_FOUND_MESSAGE);
    const headers = new Headers({
      "Content-Type": "image/jpeg",
      "Cache-Control": "no-store",
      "Content-Disposition": `inline; filename="AE_${jobId}.jpg"`,
    });
    if (file.contentLength) headers.set("Content-Length", file.contentLength);
    return new Response(file.body, { status: 200, headers });
  } catch (error) {
    return processingErrorResponse(error, { notFound: NOT_FOUND_MESSAGE, conflict: NOT_READY_MESSAGE });
  }
}
