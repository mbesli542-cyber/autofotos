/**
 * Backend contracts.
 *
 * The UI depends only on these interfaces. Two implementations exist:
 * - SupabaseDataProvider / SupabaseAuthService (production)
 * - MockDataProvider / DemoAuthService (demo mode, no backend needed)
 */
import type {
  ProcessingPresetId,
  ProcessingWarning,
  Vehicle,
  VehicleInput,
  VehiclePhoto,
  VehiclePhotoWithUrls,
  VehicleStatus,
} from "@/lib/domain/types";

export type BackendMode = "demo" | "supabase";

export interface AuthUser {
  id: string;
  email: string | null;
}

export type SignInResult = { ok: true; user: AuthUser } | { ok: false; message: string };

export interface AuthService {
  getCurrentUser(): Promise<AuthUser | null>;
  signIn(email: string, password: string): Promise<SignInResult>;
  signOut(): Promise<void>;
  /** Returns an unsubscribe function. */
  subscribe(listener: (user: AuthUser | null) => void): () => void;
}

export interface VehicleWithPhotos {
  vehicle: Vehicle;
  /** Active (non-archived) photos. */
  photos: VehiclePhoto[];
}

export interface SavePhotoInput {
  /**
   * Client-generated id. Retrying an upload with the same id is idempotent,
   * which makes the offline queue safe.
   */
  photoId: string;
  vehicleId: string;
  shotKey: string;
  shotOrder: number;
  title: string;
  file: Blob;
  thumbnail: Blob | null;
  width: number | null;
  height: number | null;
  takenAt: string;
}

export interface SaveProcessedPhotoInput {
  photo: VehiclePhoto;
  preset: ProcessingPresetId;
  file: Blob;
  /** The processor's hints on this result – they replace the stored ones. */
  warnings: readonly ProcessingWarning[];
}

/** A result the processing service already stored (Supabase contract). */
export interface RecordProcessedPhotoInput {
  photoId: string;
  preset: ProcessingPresetId;
  processedStoragePath: string;
  /** The processor's hints on this result – they replace the stored ones. */
  warnings: readonly ProcessingWarning[];
}

export interface UpdateVehicleOptions {
  /** `updatedAt` of the vehicle the input is based on (optimistic check). */
  expectedUpdatedAt?: string;
}

export interface DataProvider {
  listVehicles(): Promise<VehicleWithPhotos[]>;
  getVehicle(vehicleId: string): Promise<Vehicle | null>;
  createVehicle(input: VehicleInput): Promise<Vehicle>;
  /**
   * Saves the vehicle data. With `expectedUpdatedAt` (the `updatedAt` of the
   * vehicle the input is based on) nothing is saved when the vehicle was
   * changed since: VehicleChangedError (src/lib/errors.ts).
   */
  updateVehicle(vehicleId: string, input: VehicleInput, options?: UpdateVehicleOptions): Promise<Vehicle>;
  setVehicleStatus(vehicleId: string, status: VehicleStatus): Promise<void>;

  /** Active photos of a vehicle (unsorted – sort with the shot template). */
  listPhotos(vehicleId: string): Promise<VehiclePhoto[]>;
  /** Resolves displayable URLs (signed URLs / object URLs). */
  resolvePhotoUrls(photos: readonly VehiclePhoto[]): Promise<VehiclePhotoWithUrls[]>;
  /**
   * Stores a NEW original. An existing photo for the same shot is archived,
   * never overwritten – unless the new capture is OLDER than the active photo
   * (a late upload; the active photo counts as taken at most 5 minutes after
   * it was saved, src/lib/photos/capture-order.ts): then the new photo is
   * stored as archived and the newer one stays active.
   */
  savePhoto(input: SavePhotoInput): Promise<VehiclePhoto>;
  /** Removes a photo from the active set (the original file is kept). */
  archivePhoto(photoId: string): Promise<void>;
  /** Stores a processed version as a separate file (with the processor's hints). */
  saveProcessedPhoto(input: SaveProcessedPhotoInput): Promise<VehiclePhoto>;
  /** Links a result that the processing service already stored (with the processor's hints). */
  recordProcessedPhoto(input: RecordProcessedPhotoInput): Promise<void>;
}

export interface Backend {
  mode: BackendMode;
  auth: AuthService;
  data: DataProvider;
}
