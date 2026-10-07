/**
 * SupabaseDataProvider – production data access.
 *
 * All requests run with the signed-in user's session; Row Level Security
 * restricts access to the user's organisation (see supabase/migrations).
 * Storage buckets are private; images are shown via short-lived signed URLs.
 */
import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";
import type {
  DataProvider,
  SavePhotoInput,
  SaveProcessedPhotoInput,
  VehicleWithPhotos,
} from "@/lib/data/types";
import type {
  ProcessingPresetId,
  Vehicle,
  VehicleInput,
  VehiclePhoto,
  VehiclePhotoWithUrls,
  VehicleStatus,
} from "@/lib/domain/types";
import { AppError, isNetworkError } from "@/lib/errors";
import {
  buildOriginalStoragePath,
  buildProcessedStoragePath,
  buildThumbnailStoragePath,
  getFileExtensionForMimeType,
  STORAGE_BUCKETS,
  type StorageBucket,
} from "@/lib/naming/file-naming";
import {
  PHOTO_COLUMNS,
  rowToPhoto,
  rowToVehicle,
  VEHICLE_COLUMNS,
  vehicleInputToRow,
  type VehiclePhotoRow,
  type VehicleRow,
} from "./mappers";

const SIGNED_URL_TTL_SECONDS = 60 * 60;
/** Re-sign a little before expiry. */
const SIGNED_URL_REUSE_MS = (SIGNED_URL_TTL_SECONDS - 5 * 60) * 1000;

function toAppError(error: PostgrestError | Error | null | undefined, fallback: AppError["code"] = "unknown"): AppError {
  if (!error) return new AppError(fallback);
  if (isNetworkError(error)) return new AppError("network", { cause: error });
  const code = "code" in error ? String(error.code) : "";
  if (code === "PGRST116") return new AppError("not_found", { cause: error });
  if (code === "42501" || code === "PGRST301") return new AppError("unauthorized", { cause: error });
  if (code.startsWith("23")) return new AppError("invalid", { cause: error });
  return new AppError(fallback, { cause: error });
}

function isAlreadyExistsError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const status = (error as { statusCode?: unknown; status?: unknown }).statusCode ??
    (error as { status?: unknown }).status;
  const message = (error as { message?: unknown }).message;
  return String(status) === "409" || /already exists|duplicate/i.test(String(message ?? ""));
}

export class SupabaseDataProvider implements DataProvider {
  private signedUrlCache = new Map<string, { url: string; createdAt: number }>();

  constructor(private readonly client: SupabaseClient) {}

  async listVehicles(): Promise<VehicleWithPhotos[]> {
    const { data, error } = await this.client
      .from("vehicles")
      .select(`${VEHICLE_COLUMNS}, vehicle_photos(${PHOTO_COLUMNS})`)
      .is("vehicle_photos.archived_at", null)
      .order("created_at", { ascending: false });
    if (error) throw toAppError(error);

    return (data as (VehicleRow & { vehicle_photos: VehiclePhotoRow[] | null })[]).map((row) => ({
      vehicle: rowToVehicle(row),
      photos: (row.vehicle_photos ?? []).map(rowToPhoto),
    }));
  }

  async getVehicle(vehicleId: string): Promise<Vehicle | null> {
    const { data, error } = await this.client
      .from("vehicles")
      .select(VEHICLE_COLUMNS)
      .eq("id", vehicleId)
      .maybeSingle();
    if (error) {
      // Invalid UUIDs (22P02) simply mean "not found" for the UI.
      if (error.code === "22P02") return null;
      throw toAppError(error);
    }
    return data ? rowToVehicle(data as VehicleRow) : null;
  }

  async createVehicle(input: VehicleInput): Promise<Vehicle> {
    const { data, error } = await this.client
      .from("vehicles")
      .insert({ ...vehicleInputToRow(input), status: "new" })
      .select(VEHICLE_COLUMNS)
      .single();
    if (error) throw toAppError(error);
    return rowToVehicle(data as VehicleRow);
  }

  async updateVehicle(vehicleId: string, input: VehicleInput): Promise<Vehicle> {
    const { data, error } = await this.client
      .from("vehicles")
      .update(vehicleInputToRow(input))
      .eq("id", vehicleId)
      .select(VEHICLE_COLUMNS)
      .single();
    if (error) throw toAppError(error);
    return rowToVehicle(data as VehicleRow);
  }

  async setVehicleStatus(vehicleId: string, status: VehicleStatus): Promise<void> {
    const { error } = await this.client.from("vehicles").update({ status }).eq("id", vehicleId);
    if (error) throw toAppError(error);
  }

  async listPhotos(vehicleId: string): Promise<VehiclePhoto[]> {
    const { data, error } = await this.client
      .from("vehicle_photos")
      .select(PHOTO_COLUMNS)
      .eq("vehicle_id", vehicleId)
      .is("archived_at", null);
    if (error) throw toAppError(error);
    return (data as VehiclePhotoRow[]).map(rowToPhoto);
  }

  private async signPaths(bucket: StorageBucket, paths: string[]): Promise<Map<string, string>> {
    const result = new Map<string, string>();
    const now = Date.now();
    const missing: string[] = [];
    for (const path of new Set(paths)) {
      const cached = this.signedUrlCache.get(`${bucket}/${path}`);
      if (cached && now - cached.createdAt < SIGNED_URL_REUSE_MS) result.set(path, cached.url);
      else missing.push(path);
    }
    if (missing.length === 0) return result;

    const { data, error } = await this.client.storage
      .from(bucket)
      .createSignedUrls(missing, SIGNED_URL_TTL_SECONDS);
    if (error) throw toAppError(error);
    for (const entry of data ?? []) {
      if (entry.path && entry.signedUrl) {
        result.set(entry.path, entry.signedUrl);
        this.signedUrlCache.set(`${bucket}/${entry.path}`, { url: entry.signedUrl, createdAt: now });
      }
    }
    return result;
  }

  async resolvePhotoUrls(photos: readonly VehiclePhoto[]): Promise<VehiclePhotoWithUrls[]> {
    if (photos.length === 0) return [];
    const [originals, thumbnails, processed] = await Promise.all([
      this.signPaths(STORAGE_BUCKETS.originals, photos.map((p) => p.originalStoragePath)),
      this.signPaths(
        STORAGE_BUCKETS.thumbnails,
        photos.flatMap((p) => (p.thumbnailStoragePath ? [p.thumbnailStoragePath] : [])),
      ),
      this.signPaths(
        STORAGE_BUCKETS.processed,
        photos.flatMap((p) => (p.processedStoragePath ? [p.processedStoragePath] : [])),
      ),
    ]);
    return photos.map((photo) => {
      const original = originals.get(photo.originalStoragePath) ?? "";
      const thumbnail =
        (photo.thumbnailStoragePath && thumbnails.get(photo.thumbnailStoragePath)) || original;
      const processedUrl = photo.processedStoragePath
        ? (processed.get(photo.processedStoragePath) ?? null)
        : null;
      return { ...photo, urls: { original, thumbnail, processed: processedUrl } };
    });
  }

  private async upload(
    bucket: StorageBucket,
    path: string,
    file: Blob,
    options: { upsert: boolean },
  ): Promise<void> {
    const { error } = await this.client.storage.from(bucket).upload(path, file, {
      contentType: file.type || "image/jpeg",
      upsert: options.upsert,
      cacheControl: "31536000",
    });
    if (!error) return;
    // A retry of the same capture: the original is already stored – fine.
    if (!options.upsert && isAlreadyExistsError(error)) return;
    throw isNetworkError(error)
      ? new AppError("network", { cause: error })
      : new AppError("storage", { cause: error });
  }

  async savePhoto(input: SavePhotoInput): Promise<VehiclePhoto> {
    const extension = getFileExtensionForMimeType(input.file.type || "image/jpeg");
    const originalPath = buildOriginalStoragePath({
      vehicleId: input.vehicleId,
      shotKey: input.shotKey,
      photoId: input.photoId,
      extension,
    });
    // Originals are uploaded with upsert=false: they can never be overwritten.
    await this.upload(STORAGE_BUCKETS.originals, originalPath, input.file, { upsert: false });

    let thumbnailPath: string | null = null;
    if (input.thumbnail) {
      const path = buildThumbnailStoragePath({
        vehicleId: input.vehicleId,
        shotKey: input.shotKey,
        photoId: input.photoId,
      });
      try {
        await this.upload(STORAGE_BUCKETS.thumbnails, path, input.thumbnail, { upsert: false });
        thumbnailPath = path;
      } catch {
        // Thumbnails are optional – the original is displayed instead.
      }
    }

    // Archives the previous photo of this shot and inserts the new record
    // in one transaction (see supabase/migrations).
    const { data, error } = await this.client
      .rpc("add_vehicle_photo", {
        p_id: input.photoId,
        p_vehicle_id: input.vehicleId,
        p_shot_key: input.shotKey,
        p_shot_order: input.shotOrder,
        p_title: input.title,
        p_original_storage_path: originalPath,
        p_thumbnail_storage_path: thumbnailPath,
        p_width: input.width,
        p_height: input.height,
        p_taken_at: input.takenAt,
      })
      .single();
    if (error) throw toAppError(error, "storage");
    return rowToPhoto(data as VehiclePhotoRow);
  }

  async archivePhoto(photoId: string): Promise<void> {
    const { error } = await this.client
      .from("vehicle_photos")
      .update({ archived_at: new Date().toISOString() })
      .eq("id", photoId);
    if (error) throw toAppError(error);
  }

  async saveProcessedPhoto({ photo, preset, file }: SaveProcessedPhotoInput): Promise<VehiclePhoto> {
    const path = buildProcessedStoragePath({
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      photoId: photo.id,
      preset,
    });
    // Processed files are derivatives and may be regenerated (upsert).
    await this.upload(STORAGE_BUCKETS.processed, path, file, { upsert: true });
    await this.recordProcessedPhoto({ photoId: photo.id, preset, processedStoragePath: path });
    return { ...photo, processedStoragePath: path, processedPreset: preset };
  }

  async recordProcessedPhoto(input: {
    photoId: string;
    preset: ProcessingPresetId;
    processedStoragePath: string;
  }): Promise<void> {
    const { error } = await this.client
      .from("vehicle_photos")
      .update({
        processed_storage_path: input.processedStoragePath,
        processed_preset: input.preset,
      })
      .eq("id", input.photoId);
    if (error) throw toAppError(error);
    // A re-processed result reuses its path – make viewers fetch the new file.
    this.signedUrlCache.delete(`${STORAGE_BUCKETS.processed}/${input.processedStoragePath}`);
  }
}
