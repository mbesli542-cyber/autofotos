/**
 * Quality gate of the image processor (processor/app/pipeline/quality.py) and
 * the "Foto neu aufnehmen" decision (pure).
 *
 * The processor rejects exterior photos that would give a bad listing image
 * (vehicle too small, cropped, wrong perspective, …): the job fails with
 * `metadata.errorCode` set to one of the codes below and a German message.
 * Nothing is stored – the fix is to retake exactly that shot.
 */

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
 * Failures a new photo fixes: every quality-gate code plus the processor's
 * "vehicle not recognised" (segmentation) and "photo unreadable" (decode).
 * Server, configuration or network problems are NOT fixed by a retake.
 */
const RETAKE_ERROR_CODES: ReadonlySet<string> = new Set<string>([
  ...QUALITY_GATE_ERROR_CODES,
  "segmentation",
  "decode",
]);

export function needsRetake(errorCode: string | null | undefined): boolean {
  return typeof errorCode === "string" && RETAKE_ERROR_CODES.has(errorCode);
}

/**
 * State of a failed shot that needs a retake, from the vehicle's current
 * photos and the offline upload queue:
 * - "retake": the rejected photo is still the current one → "Foto neu aufnehmen"
 * - "saving": a new capture of the shot is still in the upload queue
 *             (`failed`: the upload failed and waits for a retry)
 * - "ready":  a new photo replaced the rejected one → it can be processed
 */
export type RetakeStatus<P> =
  | { kind: "retake" }
  | { kind: "saving"; uploadId: string; failed: boolean }
  | { kind: "ready"; photo: P };

/** null → the item did not fail or a retake would not help. */
export function getRetakeStatus<P extends { id: string; shotKey: string }>(
  item: { photoId: string; shotKey: string; status: string; errorCode: string | null },
  photos: readonly P[],
  pendingUploads: readonly { id: string; shotKey: string; status: string }[],
): RetakeStatus<P> | null {
  if (item.status !== "failed" || !needsRetake(item.errorCode)) return null;
  // The newest pending capture of this shot wins (queue is in capture order).
  const pending = pendingUploads.filter((upload) => upload.shotKey === item.shotKey).at(-1);
  if (pending) return { kind: "saving", uploadId: pending.id, failed: pending.status === "failed" };
  const current = photos.find((photo) => photo.shotKey === item.shotKey);
  if (current && current.id !== item.photoId) return { kind: "ready", photo: current };
  return { kind: "retake" };
}
