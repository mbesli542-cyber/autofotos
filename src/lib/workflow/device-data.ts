/**
 * Deleting data kept on this device (demo mode: vehicles, photos and results
 * live in this browser only). The Supabase backend never deletes vehicles
 * (no delete policy, originals are kept), so "Fahrzeug löschen" is offered
 * only where the data provider can delete (`canDeleteVehicles`).
 */
import type { DataProvider } from "@/lib/data/types";

/** A typed confirmation the employee has to enter before everything is deleted. */
export const DELETE_ALL_CONFIRMATION = "LÖSCHEN";

/** Whether the typed text confirms (pure; case and surrounding spaces do not matter). */
export function isConfirmationTyped(input: string, expected: string = DELETE_ALL_CONFIRMATION): boolean {
  return input.trim().toLocaleUpperCase("de-DE") === expected.toLocaleUpperCase("de-DE");
}

export function canDeleteVehicles(data: DataProvider): data is DataProvider & Required<Pick<DataProvider, "deleteVehicle">> {
  return typeof data.deleteVehicle === "function";
}

/** What deleting a vehicle touches besides its data (injected: the app's singletons). */
export interface VehicleDeletionDeps {
  data: DataProvider;
  /** Captures of the vehicle still waiting for their upload. */
  uploads: { removeVehicle(vehicleId: string): Promise<void> };
  /** A processing run of the vehicle ("Fotos bearbeiten") – aborted. */
  runs: { reset(vehicleId: string): void };
  /** The automatic checks of its photos – aborted, their stored rejections forgotten. */
  checks: { stopVehicle(vehicleId: string): void };
}

/**
 * "Fahrzeug löschen": stops its processing run and its automatic checks,
 * drops its waiting captures (and waits for an upload of it in progress),
 * then deletes the vehicle with its photos and results. Rejected where the
 * backend cannot delete vehicles.
 */
export async function deleteVehicleFromDevice(deps: VehicleDeletionDeps, vehicleId: string): Promise<void> {
  const { data, uploads, runs, checks } = deps;
  if (!canDeleteVehicles(data)) throw new Error("This backend does not delete vehicles.");
  runs.reset(vehicleId);
  checks.stopVehicle(vehicleId);
  await uploads.removeVehicle(vehicleId);
  await data.deleteVehicle(vehicleId);
}
