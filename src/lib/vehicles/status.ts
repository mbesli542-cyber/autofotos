import type { VehicleStatus } from "@/lib/domain/types";
import {
  getCapturedShotKeys,
  isShotSetComplete,
} from "@/lib/shots/shot-progress";
import { getRequiredShots, type ShotTemplate } from "@/lib/shots/shot-template";

export const VEHICLE_STATUS_LABELS: Record<VehicleStatus, string> = {
  new: "Neu",
  capturing: "Aufnahmen offen",
  complete: "Vollständig",
  processed: "Bearbeitet",
};

interface StatusPhoto {
  shotKey: string;
  shotOrder: number;
  processedStoragePath: string | null;
}

/**
 * Status after photos were added, retaken or deleted.
 *
 * - no photos → new
 * - required shots missing → capturing
 * - all required present: keeps "complete"; keeps "processed" only while
 *   every required photo still has a processed version (a retake resets it
 *   to "complete"). Reaching "complete" otherwise needs the explicit
 *   "Aufnahmen abschließen" action.
 */
export function deriveStatusAfterPhotoChange(
  current: VehicleStatus,
  template: ShotTemplate,
  photos: readonly StatusPhoto[],
): VehicleStatus {
  if (photos.length === 0) return "new";
  const captured = getCapturedShotKeys(photos);
  if (!isShotSetComplete(template, captured)) return "capturing";

  if (current === "processed") {
    return allRequiredProcessed(template, photos) ? "processed" : "complete";
  }
  if (current === "complete") return "complete";
  return "capturing";
}

export function allRequiredProcessed(
  template: ShotTemplate,
  photos: readonly StatusPhoto[],
): boolean {
  const byKey = new Map(photos.map((photo) => [photo.shotKey, photo]));
  return getRequiredShots(template).every(
    (shot) => byKey.get(shot.key)?.processedStoragePath != null,
  );
}

/** "Aufnahmen abschließen" is only allowed with every required shot. */
export function canCompleteCapture(
  template: ShotTemplate,
  photos: readonly { shotKey: string; shotOrder: number }[],
): boolean {
  return isShotSetComplete(template, getCapturedShotKeys(photos));
}

/** Processing is available once the photo set was marked complete. */
export function canProcessVehicle(status: VehicleStatus): boolean {
  return status === "complete" || status === "processed";
}
