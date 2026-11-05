/**
 * MockDataProvider – demo mode without any backend.
 *
 * Data lives in the browser (IndexedDB): vehicles, photo records and the
 * captured image blobs. It mirrors the Supabase behaviour (archive instead of
 * overwrite, separate processed files) so the UI is exercised realistically.
 * Processed files are only ever real results of the image processor
 * (downloaded via /api/process-job/:id/result); data migrations: ./migrations.
 */
import type {
  DataProvider,
  RecordPhotoRejectionInput,
  RecordProcessedPhotoInput,
  SavePhotoInput,
  SaveProcessedPhotoInput,
  UpdateVehicleOptions,
  VehicleReference,
  VehicleWithPhotos,
} from "@/lib/data/types";
import type {
  PhotoVersion,
  Vehicle,
  VehicleInput,
  VehiclePhoto,
  VehiclePhotoWithUrls,
  VehicleStatus,
} from "@/lib/domain/types";
import {
  AppError,
  isNetworkError,
  PHOTO_FILE_LOAD_FAILED_MESSAGE,
  PHOTO_FILE_MISSING_MESSAGE,
  storageError,
  VehicleChangedError,
} from "@/lib/errors";
import { createId } from "@/lib/ids";
import {
  buildOriginalStoragePath,
  buildProcessedStoragePath,
  buildThumbnailStoragePath,
  getFileExtensionForMimeType,
  STORAGE_BUCKETS,
} from "@/lib/naming/file-naming";
import { IdbDatabase } from "@/lib/offline/idb";
import { isOlderThanSavedPhoto } from "@/lib/photos/capture-order";
import {
  parseProcessingWarnings,
  rejectionWarning,
  storedPhotoWarnings,
} from "@/lib/processing/processing-warnings";
import { CURRENT_DEMO_DATA_VERSION, DEMO_STORES, migrateDemoData, setDemoDataVersion } from "./migrations";
import {
  buildSeedData,
  STATIC_PATH_PREFIX,
  untouchedSampleVehicleIds,
  unusedSampleVehicleIds,
  type StoredPhoto,
} from "./seed";

const STORES = DEMO_STORES;

interface StoredFile {
  path: string;
  blob: Blob;
}

const DB_NAME = "autoexperten-photo-demo";

/** The demo IndexedDB database (schema version 1; data migrations: ./migrations). */
export function createDemoDatabase(name: string = DB_NAME): IdbDatabase {
  return new IdbDatabase(name, 1, [
    { name: STORES.vehicles, keyPath: "id" },
    { name: STORES.photos, keyPath: "id", indexes: [{ name: "vehicleId", keyPath: "vehicleId" }] },
    { name: STORES.files, keyPath: "path" },
    { name: STORES.meta, keyPath: "key" },
  ]);
}

/**
 * A new `updatedAt` for a changed record: now – but always later than the
 * previous one (two changes in the same millisecond must differ, the
 * conditional update of "Fahrzeugdaten" compares them).
 */
function laterTimestamp(previous: string): string {
  const before = Date.parse(previous);
  const now = Date.now();
  return new Date(Number.isNaN(before) || now > before ? now : before + 1).toISOString();
}

/** Stored record → photo. Records from older app versions have no `processingWarnings`. */
function stripArchived(photo: StoredPhoto): VehiclePhoto {
  const rest: Partial<StoredPhoto> = { ...photo };
  delete rest.archivedAt;
  return {
    ...(rest as VehiclePhoto),
    processingWarnings: storedPhotoWarnings(photo.processedStoragePath, photo.processingWarnings),
  };
}

export class MockDataProvider implements DataProvider {
  private ready: Promise<void> | null = null;
  private objectUrls = new Map<string, string>();

  constructor(private readonly db: IdbDatabase = createDemoDatabase()) {}

  /**
   * Runs before any data is read or written: pending data migrations first
   * (e.g. removing simulated processed versions), then the demo seed (the
   * sample vehicles of an empty app, once per device) – and the samples go
   * again as soon as a real vehicle exists.
   */
  private ensureReady(): Promise<void> {
    this.ready ??= (async () => {
      await migrateDemoData(this.db);
      const marker = await this.db.get<{ key: string }>(STORES.meta, "seeded");
      if (!marker) {
        const { vehicles, photos } = buildSeedData();
        await this.db.putMany(STORES.vehicles, vehicles);
        await this.db.putMany(STORES.photos, photos);
        await this.db.put(STORES.meta, { key: "seeded", at: new Date().toISOString() });
      }
      await this.removeUnusedSamples();
    })().catch((error: unknown) => {
      this.ready = null; // retry on the next call
      throw error;
    });
    return this.ready;
  }

  /** Removes the sample vehicles nobody worked on once a real vehicle exists (seed.ts). */
  private async removeUnusedSamples(): Promise<void> {
    const [vehicles, photos] = await Promise.all([
      this.db.getAll<Vehicle>(STORES.vehicles),
      this.db.getAll<StoredPhoto>(STORES.photos),
    ]);
    const unused = unusedSampleVehicleIds(vehicles, photos);
    if (unused.length === 0) return;
    // Their photos are drawn files of the app (static paths): there are no stored files to delete.
    await this.db.deleteMany([
      { store: STORES.vehicles, keys: unused },
      ...unused.map((vehicleId) => ({ store: STORES.photos, index: { name: "vehicleId", value: vehicleId } })),
    ]);
  }

  /**
   * Deletes everything stored on this device – vehicles, photos, results –
   * for good. The sample vehicles do not come back (an empty app).
   */
  async clearAllData(): Promise<void> {
    await this.db.clear([STORES.vehicles, STORES.photos, STORES.files, STORES.meta]);
    for (const url of this.objectUrls.values()) URL.revokeObjectURL(url);
    this.objectUrls.clear();
    await setDemoDataVersion(this.db, CURRENT_DEMO_DATA_VERSION);
    await this.db.put(STORES.meta, { key: "seeded", at: new Date().toISOString() });
    this.ready = null;
    await this.ensureReady();
  }

  /**
   * Deletes a vehicle from this device for good: its record, every photo
   * record (also archived ones) and every stored file of it – originals,
   * thumbnails and processing results – in one transaction.
   */
  async deleteVehicle(vehicleId: string): Promise<void> {
    await this.ensureReady();
    const prefixes = Object.values(STORAGE_BUCKETS).map((bucket) => `${bucket}/${vehicleId}/`);
    await this.db.deleteMany([
      { store: STORES.vehicles, keys: [vehicleId] },
      { store: STORES.photos, index: { name: "vehicleId", value: vehicleId } },
      { store: STORES.files, keyPrefixes: prefixes },
    ]);
    for (const [path, url] of [...this.objectUrls]) {
      if (!prefixes.some((prefix) => path.startsWith(prefix))) continue;
      URL.revokeObjectURL(url);
      this.objectUrls.delete(path);
    }
  }

  private async activePhotos(vehicleId: string): Promise<StoredPhoto[]> {
    const photos = await this.db.getAllByIndex<StoredPhoto>(STORES.photos, "vehicleId", vehicleId);
    return photos.filter((photo) => photo.archivedAt === null);
  }

  async listVehicles(): Promise<VehicleWithPhotos[]> {
    await this.ensureReady();
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
    await this.ensureReady();
    return (await this.db.get<Vehicle>(STORES.vehicles, vehicleId)) ?? null;
  }

  async createVehicle(input: VehicleInput): Promise<Vehicle> {
    await this.ensureReady();
    const now = new Date().toISOString();
    const vehicle: Vehicle = {
      id: createId(),
      userId: "demo-user",
      ...input,
      status: "new",
      createdAt: now,
      updatedAt: now,
    };
    await this.db.put(STORES.vehicles, vehicle);
    // The first real vehicle: the samples nobody worked on are no longer needed.
    await this.removeUnusedSamples();
    return vehicle;
  }

  async updateVehicle(vehicleId: string, input: VehicleInput, options: UpdateVehicleOptions = {}): Promise<Vehicle> {
    const existing = await this.getVehicle(vehicleId);
    if (!existing) throw new AppError("not_found");
    // Like the conditional update in Supabase: never over a change made since the input's data was loaded.
    if (options.expectedUpdatedAt && existing.updatedAt !== options.expectedUpdatedAt) throw new VehicleChangedError();
    const updated: Vehicle = { ...existing, ...input, updatedAt: laterTimestamp(existing.updatedAt) };
    await this.db.put(STORES.vehicles, updated);
    return updated;
  }

  async setVehicleStatus(vehicleId: string, status: VehicleStatus): Promise<void> {
    const existing = await this.getVehicle(vehicleId);
    if (!existing) throw new AppError("not_found");
    await this.db.put(STORES.vehicles, { ...existing, status, updatedAt: laterTimestamp(existing.updatedAt) });
  }

  async listVehicleReferences(): Promise<VehicleReference[]> {
    await this.ensureReady();
    const [vehicles, photos] = await Promise.all([
      this.db.getAll<Vehicle>(STORES.vehicles),
      this.db.getAll<StoredPhoto>(STORES.photos),
    ]);
    // Untouched sample vehicles go with the first real vehicle: their numbers are free.
    const leaving = new Set(untouchedSampleVehicleIds(vehicles, photos));
    return vehicles.flatMap((vehicle) =>
      vehicle.internalReference && !leaving.has(vehicle.id)
        ? [{ id: vehicle.id, internalReference: vehicle.internalReference }]
        : [],
    );
  }

  async listPhotos(vehicleId: string): Promise<VehiclePhoto[]> {
    await this.ensureReady();
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
    await this.ensureReady();
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

  async getPhotoFile(photo: VehiclePhoto, version: PhotoVersion): Promise<Blob> {
    await this.ensureReady();
    const path = version === "processed" ? photo.processedStoragePath : photo.originalStoragePath;
    if (!path) throw new AppError("not_found", { userMessage: PHOTO_FILE_MISSING_MESSAGE });
    if (path.startsWith(STATIC_PATH_PREFIX)) {
      // Demo seed photos are files of the app itself (public/demo).
      let response: Response;
      try {
        response = await fetch(path.slice(STATIC_PATH_PREFIX.length));
      } catch (error) {
        throw isNetworkError(error)
          ? new AppError("network", { cause: error })
          : new AppError("unknown", { userMessage: PHOTO_FILE_LOAD_FAILED_MESSAGE, cause: error });
      }
      if (!response.ok) throw new AppError("not_found", { userMessage: PHOTO_FILE_MISSING_MESSAGE });
      return response.blob();
    }
    const file = await this.db.get<StoredFile>(STORES.files, path);
    if (!file) throw new AppError("not_found", { userMessage: PHOTO_FILE_MISSING_MESSAGE });
    return file.blob;
  }

  async savePhoto(input: SavePhotoInput): Promise<VehiclePhoto> {
    await this.ensureReady();
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
      throw storageError(error);
    }

    const now = new Date().toISOString();
    const active = (await this.activePhotos(input.vehicleId)).filter(
      (photo) => photo.shotKey === input.shotKey,
    );
    // A capture older than the active photo (uploaded late, e.g. a queued
    // upload retried after a retake) is kept as archived; the newer photo
    // stays. Otherwise the previous photo is archived (kept, never overwritten).
    // The same rule as add_vehicle_photo (src/lib/photos/capture-order.ts).
    const supersededOnArrival = active.some((photo) => isOlderThanSavedPhoto(input.takenAt, photo));
    if (!supersededOnArrival) {
      for (const photo of active) {
        await this.db.put<StoredPhoto>(STORES.photos, { ...photo, archivedAt: now, updatedAt: now });
      }
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
      processingWarnings: [],
      thumbnailStoragePath: thumbnailPath,
      width: input.width,
      height: input.height,
      takenAt: input.takenAt,
      createdAt: now,
      updatedAt: now,
      archivedAt: supersededOnArrival ? now : null,
    };
    await this.db.put(STORES.photos, record);
    return stripArchived(record);
  }

  async archivePhoto(photoId: string): Promise<void> {
    await this.ensureReady();
    const photo = await this.db.get<StoredPhoto>(STORES.photos, photoId);
    if (!photo) throw new AppError("not_found");
    const now = new Date().toISOString();
    await this.db.put<StoredPhoto>(STORES.photos, { ...photo, archivedAt: now, updatedAt: now });
  }

  /** Stores a REAL processor result (separate file; the original is untouched) with its hints. */
  async saveProcessedPhoto({ photo, preset, file, warnings }: SaveProcessedPhotoInput): Promise<VehiclePhoto> {
    await this.ensureReady();
    const path = `${STORAGE_BUCKETS.processed}/${buildProcessedStoragePath({
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      photoId: photo.id,
      preset,
    })}`;
    try {
      await this.db.put<StoredFile>(STORES.files, { path, blob: file });
    } catch (error) {
      throw storageError(error);
    }
    const cachedUrl = this.objectUrls.get(path);
    if (cachedUrl) {
      URL.revokeObjectURL(cachedUrl);
      this.objectUrls.delete(path);
    }
    try {
      await this.recordProcessedPhoto({ photoId: photo.id, preset, processedStoragePath: path, warnings });
    } catch (error) {
      // The photo is gone (its vehicle was deleted meanwhile): its result must not stay behind.
      if (error instanceof AppError && error.code === "not_found") {
        await this.db.delete(STORES.files, path).catch(() => undefined);
      }
      throw error;
    }
    return {
      ...photo,
      processedStoragePath: path,
      processedPreset: preset,
      processingWarnings: parseProcessingWarnings(warnings),
    };
  }

  async recordProcessedPhoto(input: RecordProcessedPhotoInput): Promise<void> {
    await this.ensureReady();
    // The hints of this result replace those of an earlier processing.
    const processingWarnings = parseProcessingWarnings(input.warnings);
    const updatedAt = new Date().toISOString();
    // Read and write in one transaction: a retake or "Foto löschen" saved
    // meanwhile (it archives this photo) is kept – only the processed fields
    // change, an archived photo never becomes active again.
    const stored = await this.db.update<StoredPhoto>(STORES.photos, input.photoId, (current) => ({
      ...current,
      processedStoragePath: input.processedStoragePath,
      processedPreset: input.preset,
      processingWarnings,
      updatedAt,
    }));
    if (!stored) throw new AppError("not_found");
  }

  async recordPhotoRejection(input: RecordPhotoRejectionInput): Promise<void> {
    await this.ensureReady();
    const rejection = parseProcessingWarnings([rejectionWarning(input.errorCode, input.message)]);
    // In one transaction: a result saved meanwhile is never marked as rejected.
    const stored = await this.db.update<StoredPhoto>(STORES.photos, input.photoId, (current) =>
      current.processedStoragePath
        ? current
        : { ...current, processingWarnings: rejection, updatedAt: new Date().toISOString() },
    );
    if (!stored) throw new AppError("not_found");
  }
}
