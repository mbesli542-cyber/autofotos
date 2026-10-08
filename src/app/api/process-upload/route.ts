/**
 * POST /api/process-upload  (demo mode; also allowed with a Supabase login)
 * multipart: file (image, ≤ 4.4 MB), preset, shotKey
 * → 202 { jobId, status }
 *
 * Forwards the photo to the processor (POST {processor}/jobs/upload) with the
 * server-side Bearer key; the browser never learns the processor URL or key.
 * The processor keeps the result; the browser downloads it via
 * GET /api/process-job/:jobId/result and stores it locally.
 */
import { NextResponse, type NextRequest } from "next/server";
import { authorizeProcessingRequest } from "@/lib/api/processing-access";
import { processingErrorResponse, processingNotConnected } from "@/lib/api/processing-errors";
import { apiError } from "@/lib/api/route-helpers";
import { isProcessingPresetId } from "@/lib/domain/types";
import { getFileExtensionForMimeType } from "@/lib/naming/file-naming";
import { getImageProcessor } from "@/lib/processing/get-image-processor";
import { PROCESS_UPLOAD_MAX_BYTES, type ProcessPhotoResponse } from "@/lib/processing/types";
import { DEFAULT_SHOT_TEMPLATE, getShot } from "@/lib/shots/shot-template";

/** Multipart overhead allowance on top of the file size limit. */
const MULTIPART_OVERHEAD_BYTES = 64 * 1024;
const MAX_MB_LABEL = (PROCESS_UPLOAD_MAX_BYTES / 1_000_000).toLocaleString("de-DE");

function formString(form: FormData, key: string): string | null {
  const value = form.get(key);
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export async function POST(request: NextRequest) {
  const tooLarge = () =>
    apiError(413, "file_too_large", `Das Foto ist zu groß für die Bildbearbeitung (maximal ${MAX_MB_LABEL} MB).`);

  // A declared length is required: a chunked body could not be bounded before
  // request.formData() buffers it (browsers always send Content-Length).
  const lengthHeader = request.headers.get("content-length");
  const declaredLength = lengthHeader === null ? Number.NaN : Number(lengthHeader);
  if (!Number.isFinite(declaredLength) || declaredLength < 0) {
    return apiError(411, "length_required", "Ungültige Anfrage. Bitte versuchen Sie es erneut.");
  }
  if (declaredLength > PROCESS_UPLOAD_MAX_BYTES + MULTIPART_OVERHEAD_BYTES) return tooLarge();

  const auth = await authorizeProcessingRequest(request);
  if (!auth.ok) return auth.response;

  const processor = getImageProcessor();
  if (!processor) return processingNotConnected();

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return apiError(400, "invalid_request", "Ungültige Anfrage. Bitte versuchen Sie es erneut.");
  }

  const file = form.get("file");
  if (!(file instanceof Blob) || file.size === 0) {
    return apiError(400, "missing_file", "Es wurde kein Foto übertragen.");
  }
  if (file.size > PROCESS_UPLOAD_MAX_BYTES) return tooLarge();
  if (!file.type.toLowerCase().startsWith("image/")) {
    return apiError(415, "unsupported_type", "Nur Bilddateien (z. B. JPG, PNG) können bearbeitet werden.");
  }

  const preset = formString(form, "preset");
  if (!isProcessingPresetId(preset)) {
    return apiError(400, "invalid_preset", "Unbekannter Bearbeitungsstil.");
  }
  const shotKey = formString(form, "shotKey");
  if (!shotKey || !getShot(DEFAULT_SHOT_TEMPLATE, shotKey)) {
    return apiError(400, "invalid_shot", "Unbekannte Aufnahmeposition.");
  }

  try {
    const job = await processor.submitUpload(
      { file, fileName: `${shotKey}.${getFileExtensionForMimeType(file.type)}`, preset, shotKey },
      request.signal,
    );
    return NextResponse.json<ProcessPhotoResponse>(
      { jobId: job.jobId, status: job.status },
      { status: 202, headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return processingErrorResponse(error);
  }
}
