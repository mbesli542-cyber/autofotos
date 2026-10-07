/**
 * POST /api/process-photo
 * Body: { vehicleId, photoId, preset }
 * → 202 { jobId, status }
 *
 * Starts a processing job. The configured ImageProcessor (mock by default)
 * does the work; see src/lib/processing.
 */
import { NextResponse, type NextRequest } from "next/server";
import { apiError, authenticateRequest } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { ProcessingServiceError } from "@/lib/processing/real-image-processor";
import {
  PROCESSING_BUSY_CODE,
  parseProcessPhotoRequest,
  type ImageProcessor,
  type ProcessPhotoResponse,
} from "@/lib/processing/types";

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return apiError(400, "invalid_json", "Ungültige Anfrage.");
  }

  const parsed = parseProcessPhotoRequest(body);
  if (!parsed.ok) return apiError(400, "invalid_request", parsed.message);

  const auth = await authenticateRequest();
  if (!auth.ok) return auth.response;

  let processor: ImageProcessor;
  try {
    processor = getImageProcessor();
  } catch {
    return apiError(503, "processing_unavailable", "Die Bildbearbeitung ist nicht konfiguriert.");
  }
  if (auth.mode === "demo" && processor.name !== "mock") {
    // Demo mode has no authentication – only the side-effect-free mock may run.
    return apiError(
      503,
      "processing_requires_auth",
      "Die Bildbearbeitung ist erst mit angebundenem Supabase-Konto verfügbar.",
    );
  }

  let shotKey: string | undefined;
  if (auth.mode === "supabase") {
    // RLS guarantees the photo belongs to the user's organisation.
    const { data, error } = await auth.client
      .from("vehicle_photos")
      .select("id, shot_key")
      .eq("id", parsed.value.photoId)
      .eq("vehicle_id", parsed.value.vehicleId)
      .is("archived_at", null)
      .maybeSingle();
    if (error || !data) return apiError(404, "not_found", "Foto wurde nicht gefunden.");
    shotKey = (data as { shot_key: string }).shot_key;
  }

  try {
    const job = await processor.submit(parsed.value, {
      userId: auth.userId,
      shotKey,
    });
    return NextResponse.json<ProcessPhotoResponse>(
      { jobId: job.jobId, status: job.status },
      { status: 202 },
    );
  } catch (error) {
    if (error instanceof ProcessingServiceError && error.status === 503) {
      // The processor queue is full – the client retries after a short wait.
      return apiError(
        503,
        PROCESSING_BUSY_CODE,
        "Die Bildbearbeitung ist gerade ausgelastet. Bitte in Kürze erneut versuchen.",
      );
    }
    return apiError(
      502,
      "processing_unavailable",
      "Die Bildbearbeitung ist derzeit nicht erreichbar. Bitte versuchen Sie es später erneut.",
    );
  }
}
