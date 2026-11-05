/**
 * "Neu aufnehmen" summaries (pure) for the review header, the completion
 * question, the vehicle page and the vehicle list. A shot counts when its
 * check state (shot-check.ts – the same state the camera and the photo tiles
 * show) is "retake": the automatic check or a processing rejected its
 * current photo, or its saved result says the photo itself is the problem.
 */
import type { VehiclePhoto, VehiclePhotoWithUrls, VehicleStatus } from "@/lib/domain/types";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import type { ShotTemplate } from "@/lib/shots/shot-template";
import type { PhotoChecksSnapshot } from "./photo-checks";
import { getShotCheckStates, type ShotCheckState } from "./shot-check";

/** Number of shots to retake among the check states of a vehicle's shots. */
export function countRetakeShots(states: Iterable<ShotCheckState>): number {
  let count = 0;
  for (const state of states) if (state.kind === "retake") count += 1;
  return count;
}

/** "1 neu aufnehmen" / "3 neu aufnehmen" (review header, next to "15 / 15 Fotos"). */
export function retakeCountLabel(count: number): string {
  return `${count} neu aufnehmen`;
}

/** Question before "Aufnahmen abschließen" while shots are red (correct plural). */
export function completeWithRetakesQuestion(count: number): string {
  return count === 1
    ? "1 Foto muss neu aufgenommen werden. Trotzdem abschließen?"
    : `${count} Fotos müssen neu aufgenommen werden. Trotzdem abschließen?`;
}

/**
 * Whether the vehicle's status chip is shown next to "Neu aufnehmen": never
 * "Vollständig" while a shot is to be retaken – the badge takes its place.
 */
export function showsStatusBadge(status: VehicleStatus, retakeCount: number): boolean {
  return !(retakeCount > 0 && status === "complete");
}

/** The check states never read a photo's URLs – lists only know the stored photos. */
const NO_URLS: VehiclePhotoWithUrls["urls"] = { original: "", thumbnail: "", processed: null };

/**
 * Keys of a vehicle's shots to retake (template order), from its active
 * photos as the vehicle list loads them (no captures waiting for their
 * upload: those belong to the vehicle page) and the automatic checks of this
 * device.
 */
export function vehicleRetakeShotKeys(
  template: ShotTemplate,
  vehicleId: string,
  photos: readonly VehiclePhoto[],
  snapshot: PhotoChecksSnapshot,
): string[] {
  const { required } = buildPhotoSlots(
    template,
    photos.map((photo) => ({ ...photo, urls: NO_URLS })),
  );
  const states = getShotCheckStates(required, vehicleId, snapshot);
  return required.filter((slot) => states.get(slot.key)?.kind === "retake").map((slot) => slot.key);
}

/**
 * Whether a vehicle belongs to the "Kamera" tab: a required shot is missing
 * or has to be retaken – a set with a red shot is never "vollständig
 * fotografiert".
 */
export function needsCamera(
  summary: { capturedRequiredCount: number; requiredCount: number },
  retakeShotKeys: readonly string[],
): boolean {
  return summary.capturedRequiredCount < summary.requiredCount || retakeShotKeys.length > 0;
}
