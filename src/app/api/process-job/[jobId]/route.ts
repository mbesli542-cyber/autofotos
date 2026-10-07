/**
 * GET /api/process-job/:jobId
 * → 200 ProcessingJob { jobId, status: queued|processing|complete|failed, … }
 */
import { NextResponse, type NextRequest } from "next/server";
import { apiError, authenticateRequest } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import type { ProcessingJob } from "@/lib/processing/types";

const JOB_ID_PATTERN = /^[A-Za-z0-9_-]{1,512}$/;

export async function GET(_request: NextRequest, ctx: RouteContext<"/api/process-job/[jobId]">) {
  const { jobId } = await ctx.params;
  if (!JOB_ID_PATTERN.test(jobId)) {
    return apiError(400, "invalid_job_id", "Ungültige Auftrags-ID.");
  }

  const auth = await authenticateRequest();
  if (!auth.ok) return auth.response;

  try {
    const job = await getImageProcessor().getJob(jobId, { userId: auth.userId });
    if (!job) return apiError(404, "not_found", "Auftrag wurde nicht gefunden.");
    return NextResponse.json<ProcessingJob>(job, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return apiError(
      502,
      "processing_unavailable",
      "Die Bildbearbeitung ist derzeit nicht erreichbar. Bitte versuchen Sie es später erneut.",
    );
  }
}
