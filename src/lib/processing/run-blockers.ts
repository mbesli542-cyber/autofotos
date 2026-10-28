/**
 * Why "Fotos verarbeiten" cannot start for a vehicle yet (pure) – in
 * addition to the processor status (canStartProcessing) and the access code.
 */
import { STATIC_PATH_PREFIX } from "@/lib/data/mock/seed";

export type ProcessingBlocker = "sample_vehicle" | "uploads_pending";

export const PROCESSING_BLOCKER_MESSAGES: Record<ProcessingBlocker, string> = {
  sample_vehicle: "Beispielfahrzeug – bitte ein eigenes Fahrzeug fotografieren.",
  uploads_pending: "Neue Fotos werden noch gespeichert. Bitte kurz warten.",
};

/** The demo sample vehicle's placeholder drawings (seed data) never give a listing photo. */
export function isPlaceholderPhoto(photo: { originalStoragePath: string }): boolean {
  return photo.originalStoragePath.startsWith(STATIC_PATH_PREFIX);
}

export function getProcessingBlocker(
  photos: readonly { originalStoragePath: string }[],
  pendingUploads: readonly { status: string }[],
): ProcessingBlocker | null {
  if (photos.some(isPlaceholderPhoto)) return "sample_vehicle";
  // Like "Aufnahmen abschließen": a capture still being saved would replace a
  // photo while (or right after) its old version is processed. Failed uploads
  // do not block – the job list offers their retry.
  if (pendingUploads.some((upload) => upload.status !== "failed")) return "uploads_pending";
  return null;
}
