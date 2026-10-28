/**
 * Workflow operations combining data access with the status rules.
 * Shared by every screen, independent of the backend implementation.
 */
import type { DataProvider, SavePhotoInput } from "@/lib/data/types";
import type { Vehicle, VehicleInput, VehiclePhoto, VehicleStatus } from "@/lib/domain/types";
import { AppError, VehicleChangedError } from "@/lib/errors";
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

/** The fields of "Fahrzeugdaten" (VehicleInput). */
const VEHICLE_DATA_FIELDS = [
  "manufacturer",
  "model",
  "color",
  "licensePlate",
  "vin",
  "mileage",
  "firstRegistration",
  "internalReference",
  "notes",
] as const satisfies readonly (keyof VehicleInput)[];

function sameVehicleData(a: VehicleInput, b: VehicleInput): boolean {
  return VEHICLE_DATA_FIELDS.every((field) => a[field] === b[field]);
}

/** Conditional saves in a row before a vehicle that keeps changing counts as changed. */
const MAX_SAVE_ATTEMPTS = 3;

/**
 * "Fahrzeugdaten": saves `input`, entered on the data of `base` (the vehicle
 * the form showed) – never over vehicle data changed since then: that
 * throws VehicleChangedError with the current vehicle. A change of the
 * status alone (e.g. a photo saved in the background) is no conflict: the
 * input is then saved on the current data.
 */
export async function saveVehicleData(
  data: DataProvider,
  vehicleId: string,
  input: VehicleInput,
  base: Vehicle,
): Promise<Vehicle> {
  let expectedUpdatedAt = base.updatedAt;
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await data.updateVehicle(vehicleId, input, { expectedUpdatedAt });
    } catch (error) {
      if (!(error instanceof VehicleChangedError)) throw error;
      const current = await data.getVehicle(vehicleId);
      if (!current) throw new AppError("not_found", { cause: error });
      // The same change was saved meanwhile (e.g. twice from two devices): done.
      if (sameVehicleData(current, input)) return current;
      if (!sameVehicleData(current, base) || attempt >= MAX_SAVE_ATTEMPTS) {
        throw new VehicleChangedError({ current, cause: error });
      }
      expectedUpdatedAt = current.updatedAt;
    }
  }
}
