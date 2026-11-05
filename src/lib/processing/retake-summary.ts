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
 * Shots of a vehicle to retake, from its active photos as the vehicle list
 * loads them (no captures waiting for their upload: those belong to the
 * vehicle page) and the automatic checks of this device.
 */
export function countVehicleRetakes(
  template: ShotTemplate,
  vehicleId: string,
  photos: readonly VehiclePhoto[],
  snapshot: PhotoChecksSnapshot,
): number {
  const { required } = buildPhotoSlots(
    template,
    photos.map((photo) => ({ ...photo, urls: NO_URLS })),
  );
  return countRetakeShots(getShotCheckStates(required, vehicleId, snapshot).values());
}
