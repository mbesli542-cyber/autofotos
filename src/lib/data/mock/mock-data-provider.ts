/**
 * MockDataProvider – demo mode without any backend.
 *
 * Data lives in the browser (IndexedDB): vehicles, photo records and the
 * captured image blobs. It mirrors the Supabase behaviour (archive instead of
 * overwrite, separate processed files) so the UI is exercised realistically.
 */
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
import { AppError } from "@/lib/errors";
import {
  buildOriginalStoragePath,
  buildProcessedStoragePath,
  buildThumbnailStoragePath,
  getFileExtensionForMimeType,
  STORAGE_BUCKETS,
} from "@/lib/naming/file-naming";
import { IdbDatabase } from "@/lib/offline/idb";
import { buildSeedData, STATIC_PATH_PREFIX, type StoredPhoto } from "./seed";

const STORES = {
  vehicles: "vehicles",
  photos: "photos",
  files: "files",
  meta: "meta",
} as const;

interface StoredFile {
  path: string;
  blob: Blob;
}

const DB_NAME = "autoexperten-photo-demo";

function stripArchived(photo: StoredPhoto): VehiclePhoto {
  const rest: Partial<StoredPhoto> = { ...photo };
  delete rest.archivedAt;
  return rest as VehiclePhoto;
}

export class MockDataProvider implements DataProvider {
  private readonly db = new IdbDatabase(DB_NAME, 1, [
    { name: STORES.vehicles, keyPath: "id" },
    { name: STORES.photos, keyPath: "id", indexes: [{ name: "vehicleId", keyPath: "vehicleId" }] },
    { name: STORES.files, keyPath: "path" },
    { name: STORES.meta, keyPath: "key" },
  ]);
  private seeding: Promise<void> | null = null;
  private objectUrls = new Map<string, string>();

  /** Seeds demo vehicles on first use. */
  private ensureSeeded(): Promise<void> {
    this.seeding ??= (async () => {
      const marker = await this.db.get<{ key: string }>(STORES.meta, "seeded");
      if (marker) return;
      const { vehicles, photos } = buildSeedData();
      await this.db.putMany(STORES.vehicles, vehicles);
      await this.db.putMany(STORES.photos, photos);
      await this.db.put(STORES.meta, { key: "seeded", at: new Date().toISOString() });
    })();
    return this.seeding;
  }

  /** Clears all demo data and restores the seed vehicles. */
  async resetDemoData(): Promise<void> {
    await this.db.clear([STORES.vehicles, STORES.photos, STORES.files, STORES.meta]);
    for (const url of this.objectUrls.values()) URL.revokeObjectURL(url);
    this.objectUrls.clear();
    this.seeding = null;
    await this.ensureSeeded();
  }

  private async activePhotos(vehicleId: string): Promise<StoredPhoto[]> {
    const photos = await this.db.getAllByIndex<StoredPhoto>(STORES.photos, "vehicleId", vehicleId);
    return photos.filter((photo) => photo.archivedAt === null);
  }

  async listVehicles(): Promise<VehicleWithPhotos[]> {
    await this.ensureSeeded();
    const [vehicles, photos] = await Promise.all([
      this.db.getAll<Vehicle>(STORES.vehicles),
      this.db.getAll<StoredPhoto>(STORES.photos),
    ]);
    const active = photos.filter((photo) => photo.archivedAt === null);
    return vehicles
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((vehicle) => ({
        vehicle,
        photos: active.filter((photo) => photo.vehicleId === vehicle.id).map(stripArchived),
      }));
  }

  async getVehicle(vehicleId: string): Promise<Vehicle | null> {
    await this.ensureSeeded();
    return (await this.db.get<Vehicle>(STORES.vehicles, vehicleId)) ?? null;
  }

  async createVehicle(input: VehicleInput): Promise<Vehicle> {
    await this.ensureSeeded();
    const now = new Date().toISOString();
    const vehicle: Vehicle = {
      id: crypto.randomUUID(),
      userId: "demo-user",
      ...input,
      status: "new",
      createdAt: now,
      updatedAt: now,
    };
    await this.db.put(STORES.vehicles, vehicle);
    return vehicle;
  }

  async updateVehicle(vehicleId: string, input: VehicleInput): Promise<Vehicle> {
    const existing = await this.getVehicle(vehicleId);
    if (!existing) throw new AppError("not_found");
    const updated: Vehicle = { ...existing, ...input, updatedAt: new Date().toISOString() };
    await this.db.put(STORES.vehicles, updated);
    return updated;
  }

  async setVehicleStatus(vehicleId: string, status: VehicleStatus): Promise<void> {
    const existing = await this.getVehicle(vehicleId);
    if (!existing) throw new AppError("not_found");
    await this.db.put(STORES.vehicles, { ...existing, status, updatedAt: new Date().toISOString() });
  }

  async listPhotos(vehicleId: string): Promise<VehiclePhoto[]> {
    await this.ensureSeeded();
    return (await this.activePhotos(vehicleId)).map(stripArchived);
  }

  private async urlFor(path: string): Promise<string | null> {
    if (path.startsWith(STATIC_PATH_PREFIX)) return path.slice(STATIC_PATH_PREFIX.length);
    const cached = this.objectUrls.get(path);
    if (cached) return cached;
    const file = await this.db.get<StoredFile>(STORES.files, path);
    if (!file) return null;
    const url = URL.createObjectURL(file.blob);
    this.objectUrls.set(path, url);
    return url;
  }

  async resolvePhotoUrls(photos: readonly VehiclePhoto[]): Promise<VehiclePhotoWithUrls[]> {
    return Promise.all(
      photos.map(async (photo) => {
        const original = (await this.urlFor(photo.originalStoragePath)) ?? "";
        const thumbnail = photo.thumbnailStoragePath
          ? await this.urlFor(photo.thumbnailStoragePath)
          : null;
        const processed = photo.processedStoragePath
          ? await this.urlFor(photo.processedStoragePath)
          : null;
        return { ...photo, urls: { original, thumbnail: thumbnail ?? original, processed } };
      }),
    );
  }

  async savePhoto(input: SavePhotoInput): Promise<VehiclePhoto> {
    const existingRecord = await this.db.get<StoredPhoto>(STORES.photos, input.photoId);
    if (existingRecord) return stripArchived(existingRecord); // idempotent retry

    const vehicle = await this.getVehicle(input.vehicleId);
    if (!vehicle) throw new AppError("not_found");

    const extension = getFileExtensionForMimeType(input.file.type || "image/jpeg");
    const originalPath = `${STORAGE_BUCKETS.originals}/${buildOriginalStoragePath({
      vehicleId: input.vehicleId,
      shotKey: input.shotKey,
      photoId: input.photoId,
      extension,
    })}`;
    const thumbnailPath = input.thumbnail
      ? `${STORAGE_BUCKETS.thumbnails}/${buildThumbnailStoragePath({
          vehicleId: input.vehicleId,
          shotKey: input.shotKey,
          photoId: input.photoId,
        })}`
      : null;

    try {
      // Originals are written once and never replaced.
      if (!(await this.db.get<StoredFile>(STORES.files, originalPath))) {
        await this.db.put<StoredFile>(STORES.files, { path: originalPath, blob: input.file });
      }
      if (input.thumbnail && thumbnailPath) {
        await this.db.put<StoredFile>(STORES.files, { path: thumbnailPath, blob: input.thumbnail });
      }
    } catch (error) {
      throw new AppError("storage", { cause: error });
    }

    const now = new Date().toISOString();
    // Archive the previous photo of this shot (kept, never overwritten).
    const previous = (await this.activePhotos(input.vehicleId)).filter(
      (photo) => photo.shotKey === input.shotKey,
    );
    for (const photo of previous) {
      await this.db.put<StoredPhoto>(STORES.photos, { ...photo, archivedAt: now, updatedAt: now });
    }

    const record: StoredPhoto = {
      id: input.photoId,
      vehicleId: input.vehicleId,
      shotKey: input.shotKey,
      shotOrder: input.shotOrder,
      title: input.title,
      originalStoragePath: originalPath,
      processedStoragePath: null,
      processedPreset: null,
      thumbnailStoragePath: thumbnailPath,
      width: input.width,
      height: input.height,
      takenAt: input.takenAt,
      createdAt: now,
      updatedAt: now,
      archivedAt: null,
    };
    await this.db.put(STORES.photos, record);
    return stripArchived(record);
  }

  async archivePhoto(photoId: string): Promise<void> {
    const photo = await this.db.get<StoredPhoto>(STORES.photos, photoId);
    if (!photo) throw new AppError("not_found");
    const now = new Date().toISOString();
    await this.db.put<StoredPhoto>(STORES.photos, { ...photo, archivedAt: now, updatedAt: now });
  }

  async saveProcessedPhoto({ photo, preset, file }: SaveProcessedPhotoInput): Promise<VehiclePhoto> {
    const path = `${STORAGE_BUCKETS.processed}/${buildProcessedStoragePath({
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      photoId: photo.id,
      preset,
    })}`;
    try {
      await this.db.put<StoredFile>(STORES.files, { path, blob: file });
    } catch (error) {
      throw new AppError("storage", { cause: error });
    }
    const cachedUrl = this.objectUrls.get(path);
    if (cachedUrl) {
      URL.revokeObjectURL(cachedUrl);
      this.objectUrls.delete(path);
    }
    await this.recordProcessedPhoto({ photoId: photo.id, preset, processedStoragePath: path });
    return { ...photo, processedStoragePath: path, processedPreset: preset };
  }

  async recordProcessedPhoto(input: {
    photoId: string;
    preset: ProcessingPresetId;
    processedStoragePath: string;
  }): Promise<void> {
    const stored = await this.db.get<StoredPhoto>(STORES.photos, input.photoId);
    if (!stored) throw new AppError("not_found");
    await this.db.put<StoredPhoto>(STORES.photos, {
      ...stored,
      processedStoragePath: input.processedStoragePath,
      processedPreset: input.preset,
      updatedAt: new Date().toISOString(),
    });
  }
}
