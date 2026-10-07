/**
 * Workflow operations combining data access with the status rules.
 * Shared by every screen, independent of the backend implementation.
 */
import type { DataProvider, SavePhotoInput } from "@/lib/data/types";
import type { VehiclePhoto, VehicleStatus } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import type { ShotTemplate } from "@/lib/shots/shot-template";
import {
  allRequiredProcessed,
  canCompleteCapture,
  deriveStatusAfterPhotoChange,
} from "@/lib/vehicles/status";

async function syncStatus(
  data: DataProvider,
  template: ShotTemplate,
  vehicleId: string,
): Promise<{ status: VehicleStatus; photos: VehiclePhoto[] }> {
  const [vehicle, photos] = await Promise.all([
    data.getVehicle(vehicleId),
    data.listPhotos(vehicleId),
  ]);
  if (!vehicle) throw new AppError("not_found");
  const status = deriveStatusAfterPhotoChange(vehicle.status, template, photos);
  if (status !== vehicle.status) await data.setVehicleStatus(vehicleId, status);
  return { status, photos };
}

/** Saves a captured photo (new original) and updates the vehicle status. */
export async function savePhotoAndSyncStatus(
  data: DataProvider,
  template: ShotTemplate,
  input: SavePhotoInput,
): Promise<{ photo: VehiclePhoto; status: VehicleStatus }> {
  const photo = await data.savePhoto(input);
  const { status } = await syncStatus(data, template, input.vehicleId);
  return { photo, status };
}

/** "Foto löschen": removes the photo from the set (original file is kept). */
export async function deletePhotoAndSyncStatus(
  data: DataProvider,
  template: ShotTemplate,
  photo: VehiclePhoto,
): Promise<VehicleStatus> {
  await data.archivePhoto(photo.id);
  return (await syncStatus(data, template, photo.vehicleId)).status;
}

/** "Aufnahmen abschließen" – only with all required shots present. */
export async function completeCapture(
  data: DataProvider,
  template: ShotTemplate,
  vehicleId: string,
): Promise<void> {
  const photos = await data.listPhotos(vehicleId);
  if (!canCompleteCapture(template, photos)) {
    throw new AppError("invalid", {
      userMessage: "Es fehlen noch Pflichtfotos. Bitte vervollständigen Sie die Aufnahmen.",
    });
  }
  const vehicle = await data.getVehicle(vehicleId);
  if (!vehicle) throw new AppError("not_found");
  if (vehicle.status !== "complete" && vehicle.status !== "processed") {
    await data.setVehicleStatus(vehicleId, "complete");
  }
}

/** Marks the vehicle as processed once every required photo has a result. */
export async function markProcessedIfDone(
  data: DataProvider,
  template: ShotTemplate,
  vehicleId: string,
): Promise<boolean> {
  const photos = await data.listPhotos(vehicleId);
  if (!allRequiredProcessed(template, photos)) return false;
  await data.setVehicleStatus(vehicleId, "processed");
  return true;
}
