/** Row ⇄ domain mapping for the Supabase tables (snake_case ⇄ camelCase). */
import {
  isProcessingPresetId,
  isVehicleStatus,
  type Vehicle,
  type VehicleInput,
  type VehiclePhoto,
} from "@/lib/domain/types";

export interface VehicleRow {
  id: string;
  user_id: string | null;
  manufacturer: string;
  model: string;
  color: string | null;
  license_plate: string | null;
  vin: string | null;
  mileage: number | null;
  first_registration: string | null;
  internal_reference: string | null;
  notes: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface VehiclePhotoRow {
  id: string;
  vehicle_id: string;
  shot_key: string;
  shot_order: number;
  title: string;
  original_storage_path: string;
  processed_storage_path: string | null;
  processed_preset: string | null;
  thumbnail_storage_path: string | null;
  width: number | null;
  height: number | null;
  taken_at: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export const VEHICLE_COLUMNS =
  "id, user_id, manufacturer, model, color, license_plate, vin, mileage, first_registration, internal_reference, notes, status, created_at, updated_at";

export const PHOTO_COLUMNS =
  "id, vehicle_id, shot_key, shot_order, title, original_storage_path, processed_storage_path, processed_preset, thumbnail_storage_path, width, height, taken_at, archived_at, created_at, updated_at";

export function rowToVehicle(row: VehicleRow): Vehicle {
  return {
    id: row.id,
    userId: row.user_id,
    manufacturer: row.manufacturer,
    model: row.model,
    color: row.color,
    licensePlate: row.license_plate,
    vin: row.vin,
    mileage: row.mileage,
    firstRegistration: row.first_registration,
    internalReference: row.internal_reference,
    notes: row.notes,
    status: isVehicleStatus(row.status) ? row.status : "new",
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export function vehicleInputToRow(input: VehicleInput) {
  return {
    manufacturer: input.manufacturer,
    model: input.model,
    color: input.color,
    license_plate: input.licensePlate,
    vin: input.vin,
    mileage: input.mileage,
    first_registration: input.firstRegistration,
    internal_reference: input.internalReference,
    notes: input.notes,
  };
}

export function rowToPhoto(row: VehiclePhotoRow): VehiclePhoto {
  return {
    id: row.id,
    vehicleId: row.vehicle_id,
    shotKey: row.shot_key,
    shotOrder: row.shot_order,
    title: row.title,
    originalStoragePath: row.original_storage_path,
    processedStoragePath: row.processed_storage_path,
    processedPreset: isProcessingPresetId(row.processed_preset) ? row.processed_preset : null,
    thumbnailStoragePath: row.thumbnail_storage_path,
    width: row.width,
    height: row.height,
    takenAt: row.taken_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}
