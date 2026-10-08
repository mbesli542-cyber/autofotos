/**
 * GET /api/processing-status
 * → 200 { connected, processor, showroomSource, showroomError, accessCodeRequired, dataBackend }
 *
 * Pings the processor's authenticated GET /health (short timeout) so the
 * processing page only offers "Fotos verarbeiten" when real showroom
 * processing can actually run. Never reveals the processor URL or key.
 */
import { NextResponse, connection } from "next/server";
import { getProcessingAccessCode } from "@/lib/api/processing-access";
import { authenticateRequest } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { buildProcessingStatus } from "@/lib/processing/processing-status";
import { HEALTH_TIMEOUT_MS } from "@/lib/processing/real-image-processor";
import type { ProcessingStatus } from "@/lib/processing/types";

export async function GET() {
  await connection(); // runtime env + live health check – never prerender

  const auth = await authenticateRequest();
  if (!auth.ok) return auth.response;

  const processor = getImageProcessor();
  const health = processor ? await processor.getHealth(HEALTH_TIMEOUT_MS) : null;
  if (processor && !health?.ok) {
    console.warn("[processing] Processor health check failed or the processor is not ready.");
  } else if (health && !health.authorized) {
    console.warn("[processing] The processor did not accept IMAGE_PROCESSING_API_KEY (/health without details).");
  }

  const status = buildProcessingStatus({
    processor: processor ? "real" : "mock",
    health,
    accessCodeRequired: auth.mode === "demo" && getProcessingAccessCode() !== null,
    dataBackend: auth.mode,
  });
  return NextResponse.json<ProcessingStatus>(status, { headers: { "Cache-Control": "no-store" } });
}
