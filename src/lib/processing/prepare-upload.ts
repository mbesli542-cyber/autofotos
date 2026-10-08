/**
 * Demo mode: prepares an original photo for POST /api/process-upload.
 *
 * Vercel functions accept at most 4.5 MB per request, so the browser sends
 * a COPY of the original that stays well below that: long edge ≤ 3200 px
 * (the processor's output size), JPEG quality 0.9, re-encoded with lower
 * quality if still > 4.0 MB. EXIF orientation is applied while decoding
 * (`createImageBitmap(blob, { imageOrientation: "from-image" })`). The
 * stored original is never changed.
 */
import { canvasToBlob, decodeImage, fitWithin } from "@/lib/camera/image-utils";
import { AppError } from "@/lib/errors";
import { PROCESS_UPLOAD_MAX_LONG_EDGE, PROCESS_UPLOAD_TARGET_BYTES } from "./types";

export const UPLOAD_JPEG_QUALITIES = [0.9, 0.8, 0.7, 0.6] as const;

/** Formats the processor decodes and that may be sent unchanged when small enough. */
const PASS_THROUGH_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

export interface UploadLimits {
  maxLongEdge: number;
  maxBytes: number;
}

export const DEFAULT_UPLOAD_LIMITS: UploadLimits = {
  maxLongEdge: PROCESS_UPLOAD_MAX_LONG_EDGE,
  maxBytes: PROCESS_UPLOAD_TARGET_BYTES,
};

export const UPLOAD_TOO_LARGE_MESSAGE =
  "Das Foto ist zu groß für die Bildbearbeitung und konnte nicht verkleinert werden.";

/**
 * Pure decision: send the original as is, or downscale/re-encode it.
 * `dimensions` is null when the browser cannot decode the image.
 */
export function planUpload(
  file: { size: number; type: string },
  dimensions: { width: number; height: number } | null,
  limits: UploadLimits = DEFAULT_UPLOAD_LIMITS,
): "send_original" | "reencode" | "too_large" {
  const smallEnough = file.size > 0 && file.size <= limits.maxBytes;
  if (!dimensions) return smallEnough ? "send_original" : "too_large";
  const fits = Math.max(dimensions.width, dimensions.height) <= limits.maxLongEdge;
  return smallEnough && fits && PASS_THROUGH_TYPES.has(file.type.toLowerCase()) ? "send_original" : "reencode";
}

export async function prepareProcessingUpload(
  original: Blob,
  limits: UploadLimits = DEFAULT_UPLOAD_LIMITS,
): Promise<Blob> {
  let decoded: Awaited<ReturnType<typeof decodeImage>> | null = null;
  try {
    decoded = await decodeImage(original);
  } catch {
    decoded = null;
  }
  const plan = planUpload(original, decoded, limits);
  if (plan !== "reencode" || !decoded) {
    decoded?.release();
    if (plan === "send_original") return original;
    throw new AppError("invalid", { userMessage: UPLOAD_TOO_LARGE_MESSAGE });
  }

  try {
    const size = fitWithin(decoded.width, decoded.height, limits.maxLongEdge);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    if (!context) throw new AppError("invalid", { userMessage: UPLOAD_TOO_LARGE_MESSAGE });
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(decoded.source, 0, 0, size.width, size.height);
    for (const quality of UPLOAD_JPEG_QUALITIES) {
      const blob = await canvasToBlob(canvas, "image/jpeg", quality);
      if (blob.size <= limits.maxBytes) return blob;
    }
    throw new AppError("invalid", { userMessage: UPLOAD_TOO_LARGE_MESSAGE });
  } finally {
    decoded.release();
  }
}

/** The stored original photo (object URL in demo mode, signed URL otherwise). */
export async function loadOriginalPhoto(url: string, signal?: AbortSignal): Promise<Blob> {
  if (!url) throw new AppError("not_found", { userMessage: "Das Originalfoto wurde nicht gefunden." });
  let response: Response;
  try {
    response = await fetch(url, { signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new AppError("storage", { userMessage: "Das Originalfoto konnte nicht geladen werden.", cause: error });
  }
  if (!response.ok) throw new AppError("storage", { userMessage: "Das Originalfoto konnte nicht geladen werden." });
  return response.blob();
}
