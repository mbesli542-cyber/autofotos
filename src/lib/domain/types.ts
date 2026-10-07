/**
 * Core domain types shared by the UI, the data providers and the processing
 * layer. Field names are camelCase here; the Supabase provider maps them from
 * the snake_case database columns.
 */

export const VEHICLE_STATUSES = [
  "new",
  "capturing",
  "complete",
  "processed",
] as const;

export type VehicleStatus = (typeof VEHICLE_STATUSES)[number];

export function isVehicleStatus(value: unknown): value is VehicleStatus {
  return (
    typeof value === "string" &&
    (VEHICLE_STATUSES as readonly string[]).includes(value)
  );
}

export interface Vehicle {
  id: string;
  /** Employee who created the vehicle. */
  userId: string | null;
  manufacturer: string;
  model: string;
  color: string | null;
  licensePlate: string | null;
  vin: string | null;
  mileage: number | null;
  /** ISO date (YYYY-MM-DD). */
  firstRegistration: string | null;
  internalReference: string | null;
  notes: string | null;
  status: VehicleStatus;
  createdAt: string;
  updatedAt: string;
}

/** Normalised, validated vehicle data (output of the form validation). */
export interface VehicleInput {
  manufacturer: string;
  model: string;
  color: string | null;
  licensePlate: string | null;
  vin: string | null;
  mileage: number | null;
  firstRegistration: string | null;
  internalReference: string | null;
  notes: string | null;
}

export const PROCESSING_PRESET_IDS = [
  "autoexperten_standard",
  "autoexperten_dark",
  "original_plus",
] as const;

export type ProcessingPresetId = (typeof PROCESSING_PRESET_IDS)[number];

export function isProcessingPresetId(
  value: unknown,
): value is ProcessingPresetId {
  return (
    typeof value === "string" &&
    (PROCESSING_PRESET_IDS as readonly string[]).includes(value)
  );
}

/**
 * One captured photo for one shot of a vehicle.
 *
 * `originalStoragePath` is immutable: a retake creates a NEW photo record and
 * archives the previous one. Processed results are always separate files.
 */
export interface VehiclePhoto {
  id: string;
  vehicleId: string;
  shotKey: string;
  shotOrder: number;
  title: string;
  originalStoragePath: string;
  processedStoragePath: string | null;
  processedPreset: ProcessingPresetId | null;
  thumbnailStoragePath: string | null;
  width: number | null;
  height: number | null;
  takenAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface PhotoUrls {
  original: string;
  /** Falls back to the original if no thumbnail exists. */
  thumbnail: string;
  processed: string | null;
}

export interface VehiclePhotoWithUrls extends VehiclePhoto {
  urls: PhotoUrls;
}

/** Minimal shape needed by the shot progress logic. */
export interface ShotPhotoRef {
  shotKey: string;
  shotOrder: number;
}

export interface VehicleSummary {
  vehicle: Vehicle;
  capturedRequiredCount: number;
  requiredCount: number;
  thumbnailUrl: string | null;
}
