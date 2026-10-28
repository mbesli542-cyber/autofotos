/**
 * Quality gate of the image processor (processor/app/pipeline/quality.py) and
 * the "Foto neu aufnehmen" decision (pure).
 *
 * The processor rejects exterior photos that would give a bad listing image
 * (vehicle too small, cropped, wrong perspective, …): the job fails with
 * `metadata.errorCode` set to one of the codes below and a German message.
 * Nothing is stored – the fix is to retake exactly that shot.
 */

import type { ProcessingWarning } from "@/lib/domain/types";
import { isOlderThanSavedPhoto } from "@/lib/photos/capture-order";
import { hasRetakeWarning } from "./processing-warnings";

export const QUALITY_GATE_ERROR_CODES = [
  "source_resolution_too_low",
  "vehicle_too_small",
  "vehicle_cropped",
  "mask_low_confidence",
  "ground_contact_uncertain",
  "perspective_mismatch",
] as const;

export type QualityGateErrorCode = (typeof QUALITY_GATE_ERROR_CODES)[number];

/**
 * The processor's German messages (identical strings). Only used when a
 * failed job carries a quality-gate code but no usable message.
 */
export const QUALITY_GATE_MESSAGES: Record<QualityGateErrorCode, string> = {
  source_resolution_too_low:
    "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen.",
  vehicle_too_small: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
  vehicle_cropped:
    "Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren.",
  mask_low_confidence:
    "Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen.",
  ground_contact_uncertain:
    "Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein.",
  perspective_mismatch:
    "Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren.",
};

export function isQualityGateErrorCode(value: unknown): value is QualityGateErrorCode {
  return QUALITY_GATE_ERROR_CODES.some((code) => code === value);
}

/**
 * The processor downscales very large JPEGs itself; other photos above its
 * limit (80 MP) fail with this code – a photo in normal camera resolution fixes it.
 */
export const IMAGE_TOO_LARGE_ERROR_CODE = "image_too_large";
export const IMAGE_TOO_LARGE_MESSAGE =
  "Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler Kamera-Auflösung neu aufnehmen.";

/**
 * Failures a new photo fixes: every quality-gate code plus the processor's
 * "vehicle not recognised" (segmentation), "photo unreadable" (decode) and
 * "resolution too high" (image_too_large).
 * Server, configuration or network problems are NOT fixed by a retake.
 */
const RETAKE_ERROR_CODES: ReadonlySet<string> = new Set<string>([
  ...QUALITY_GATE_ERROR_CODES,
  "segmentation",
  "decode",
  IMAGE_TOO_LARGE_ERROR_CODE,
]);

export function needsRetake(errorCode: string | null | undefined): boolean {
  return typeof errorCode === "string" && RETAKE_ERROR_CODES.has(errorCode);
}

/**
 * The German retake instruction for a failed job that carries such a code
 * but no usable message (null for other codes).
 */
export function retakeFallbackMessage(errorCode: string | null | undefined): string | null {
  if (isQualityGateErrorCode(errorCode)) return QUALITY_GATE_MESSAGES[errorCode];
  return errorCode === IMAGE_TOO_LARGE_ERROR_CODE ? IMAGE_TOO_LARGE_MESSAGE : null;
}

/**
 * State of a finished shot (complete or failed) whose photo a new one
 * fixes or replaced, from the vehicle's current photos and the offline
 * upload queue:
 * - "retake": the rejected photo – or a processed one whose result carries a
 *             retake hint (e.g. it shows the other side of the car) – is still
 *             the current one → "Foto neu aufnehmen"
 * - "saving": a new capture of the shot is still in the upload queue
 *             (`failed`: the upload failed and waits for a retry, `error`: why);
 *             a capture older than the saved photo does not count – it never
 *             replaces it
 * - "ready":  a new photo replaced the processed or rejected one (e.g. a
 *             retake saved while the run was going on) → it can be processed
 */
export type RetakeStatus<P> =
  | { kind: "retake" }
  | { kind: "saving"; uploadId: string; failed: boolean; error: string | null }
  | { kind: "ready"; photo: P };

/** A pending capture as the upload queue reports it (src/lib/offline/upload-queue.ts). */
interface PendingCapture {
  id: string;
  shotKey: string;
  status: string;
  /** Capture time (ISO): a capture older than the shot's saved photo never becomes its photo. */
  takenAt: string;
  /** false: the server refused the file for good – it never becomes the shot's photo. */
  retryable?: boolean;
  error?: string | null;
}

/** null → the item is still running, or its photo is current and a retake would not help. */
export function getRetakeStatus<P extends { id: string; shotKey: string; takenAt: string; createdAt: string }>(
  item: {
    photoId: string;
    shotKey: string;
    status: string;
    errorCode: string | null;
    /** The processor's hints on a saved result (processing-warnings.ts). */
    warnings?: readonly ProcessingWarning[];
  },
  photos: readonly P[],
  pendingUploads: readonly PendingCapture[],
): RetakeStatus<P> | null {
  if (item.status !== "complete" && item.status !== "failed") return null;
  const current = photos.find((photo) => photo.shotKey === item.shotKey);
  // The newest pending capture of this shot wins (queue is in capture order). A capture
  // the server refused for good is left to "Fotos überprüfen" (discard there); one older
  // than the shot's saved photo is stored archived – decided like the photo slots and the
  // database (src/lib/photos/capture-order.ts).
  const pending = pendingUploads
    .filter(
      (upload) =>
        upload.shotKey === item.shotKey &&
        !(upload.status === "failed" && upload.retryable === false) &&
        !(current && isOlderThanSavedPhoto(upload.takenAt, current)),
    )
    .at(-1);
  if (pending) {
    const failed = pending.status === "failed";
    return { kind: "saving", uploadId: pending.id, failed, error: failed ? (pending.error ?? null) : null };
  }
  if (current && current.id !== item.photoId) return { kind: "ready", photo: current };
  if (item.status === "failed") return needsRetake(item.errorCode) ? { kind: "retake" } : null;
  return hasRetakeWarning(item.warnings ?? []) ? { kind: "retake" } : null;
}
