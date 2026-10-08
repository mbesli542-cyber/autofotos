/**
 * POST /api/process-photo  (Supabase mode)
 * Body: { vehicleId, photoId, preset }
 * → 202 { jobId, status }
 *
 * The processor reads the original from Supabase storage and stores the
 * result itself (`result: { kind: "stored" }`). Demo mode uses
 * POST /api/process-upload instead (photos only exist in the browser).
 * Without a connected processor: 503 "Echte Showroom-Bearbeitung ist noch nicht verbunden."
 */
import { NextResponse, type NextRequest } from "next/server";
import { processingErrorResponse, processingNotConnected } from "@/lib/api/processing-errors";
import { apiError, authenticateRequest } from "@/lib/api/route-helpers";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { parseProcessPhotoRequest, type ProcessPhotoResponse } from "@/lib/processing/types";

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
  if (auth.mode === "demo") {
    return apiError(
      409,
      "upload_required",
      "Im Demo-Modus werden die Fotos zur Bearbeitung direkt hochgeladen. Bitte laden Sie die Seite neu.",
    );
  }

  const processor = getImageProcessor();
  if (!processor) return processingNotConnected();

  // RLS guarantees the photo belongs to the user's organisation.
  const { data, error } = await auth.client
    .from("vehicle_photos")
    .select("id, shot_key")
    .eq("id", parsed.value.photoId)
    .eq("vehicle_id", parsed.value.vehicleId)
    .is("archived_at", null)
    .maybeSingle();
  if (error || !data) return apiError(404, "not_found", "Foto wurde nicht gefunden.");
  const shotKey = (data as { shot_key: string }).shot_key;

  try {
    const job = await processor.submit(parsed.value, { userId: auth.userId, shotKey });
    return NextResponse.json<ProcessPhotoResponse>(
      { jobId: job.jobId, status: job.status },
      { status: 202, headers: { "Cache-Control": "no-store" } },
    );
  } catch (submitError) {
    return processingErrorResponse(submitError);
  }
}
