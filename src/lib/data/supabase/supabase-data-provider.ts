/**
 * SupabaseDataProvider – production data access.
 *
 * All requests run with the signed-in user's session; Row Level Security
 * restricts access to the user's organisation (see supabase/migrations).
 * Storage buckets are private; images are shown via signed URLs (valid for
 * two hours; a cached URL is reused only during its first 30 minutes).
 */
import type { PostgrestError, SupabaseClient } from "@supabase/supabase-js";
import type {
  DataProvider,
  RecordProcessedPhotoInput,
  SavePhotoInput,
  SaveProcessedPhotoInput,
  UpdateVehicleOptions,
  VehicleWithPhotos,
} from "@/lib/data/types";
import type {
  Vehicle,
  VehicleInput,
  VehiclePhoto,
  VehiclePhotoWithUrls,
  VehicleStatus,
} from "@/lib/domain/types";
import { AppError, isNetworkError, VehicleChangedError } from "@/lib/errors";
import {
  buildOriginalStoragePath,
  buildProcessedStoragePath,
  buildThumbnailStoragePath,
  getFileExtensionForMimeType,
  normalizeImageMimeType,
  STORAGE_BUCKETS,
  type StorageBucket,
} from "@/lib/naming/file-naming";
import { parseProcessingWarnings } from "@/lib/processing/processing-warnings";
import {
  PHOTO_COLUMNS,
  rowToPhoto,
  rowToVehicle,
  VEHICLE_COLUMNS,
  vehicleInputToRow,
  type VehiclePhotoRow,
  type VehicleRow,
} from "./mappers";

const SIGNED_URL_TTL_SECONDS = 2 * 60 * 60;
/**
 * A cached URL is only handed out while at least this much validity is left
 * (so it is reused during its first 30 minutes): pages keep URLs in state,
 * and lazily loaded images and the large view request them much later.
 */
const SIGNED_URL_MIN_REMAINING_MS = 90 * 60 * 1000;
/**
 * Vehicles per list request. The Data API silently caps every response at
 * its max-rows setting (Supabase default 1000, possibly lower), so the list
 * is read in pages until no vehicle is left.
 */
const VEHICLE_PAGE_SIZE = 500;

type VehicleListRow = VehicleRow & { vehicle_photos: VehiclePhotoRow[] | null };

function toAppError(error: PostgrestError | Error | null | undefined, fallback: AppError["code"] = "unknown"): AppError {
  if (!error) return new AppError(fallback);
  if (isNetworkError(error)) return new AppError("network", { cause: error });
  const code = "code" in error ? String(error.code) : "";
  if (code === "PGRST116") return new AppError("not_found", { cause: error });
  if (code === "42501" || code === "PGRST301") return new AppError("unauthorized", { cause: error });
  if (code.startsWith("23")) return new AppError("invalid", { cause: error });
  return new AppError(fallback, { cause: error });
}

/**
 * The photo columns without `processing_warnings`. That column comes with
 * supabase/migrations/20261028000100_photo_processing_warnings.sql – until
 * it is applied, photos are read and recorded without the processor's hints
 * instead of every vehicle and photo request failing.
 */
const PHOTO_COLUMNS_WITHOUT_WARNINGS = PHOTO_COLUMNS.split(", ")
  .filter((column) => column !== "processing_warnings")
  .join(", ");

/** The database has no `processing_warnings` column (42703 when read, PGRST204 when written). */
function isMissingWarningsColumn(error: PostgrestError | null | undefined): boolean {
  if (!error) return false;
  const code = String(error.code ?? "");
  const text = `${error.message ?? ""} ${error.details ?? ""}`;
  return (code === "42703" || code === "PGRST204") && text.includes("processing_warnings");
}

function isAlreadyExistsError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const status = (error as { statusCode?: unknown; status?: unknown }).statusCode ??
    (error as { status?: unknown }).status;
  const message = (error as { message?: unknown }).message;
  return String(status) === "409" || /already exists|duplicate/i.test(String(message ?? ""));
}

/**
 * Storage upload error → AppError. Files the bucket refuses (type outside its
 * allowlist, larger than its size limit) are final – another attempt cannot
 * succeed. Everything else (network, session/RLS, server) may be retried.
 * Older Storage versions answer with HTTP 400 and the real code in statusCode.
 */
function toStorageAppError(error: unknown): AppError {
  if (isNetworkError(error)) return new AppError("network", { cause: error });
  const { status, statusCode, code, message } = (error ?? {}) as Record<string, unknown>;
  const statuses = [String(status), String(statusCode)];
  const text = `${String(code ?? "")} ${String(message ?? "")}`;
  if (statuses.includes("415") || /invalid_?mime_?type|mime type .* not supported/i.test(text)) {
    return new AppError("unsupported_file", { cause: error });
  }
  if (statuses.includes("413") || /entity_?too_?large|payload too large|exceeded the maximum allowed size/i.test(text)) {
    return new AppError("file_too_large", { cause: error });
  }
  return new AppError("storage", { cause: error });
}

export class SupabaseDataProvider implements DataProvider {
  private signedUrlCache = new Map<string, { url: string; expiresAt: number }>();
  /** false once the database turned out not to have the hints column yet (migration missing). */
  private storesWarnings = true;

  constructor(private readonly client: SupabaseClient) {}

  /** Notes that the hints column is missing – from now on photos are read and recorded without it. */
  private noteMissingWarningsColumn(): void {
    if (!this.storesWarnings) return;
    this.storesWarnings = false;
    console.warn(
      "[AutoExperten Photo] vehicle_photos.processing_warnings is missing – apply supabase/migrations/20261028000100_photo_processing_warnings.sql. Until then the processor's hints are not stored.",
    );
  }

  /**
   * Runs a request that reads photo columns – once more without the hints
   * column if the database does not have it yet.
   */
  private async readPhotoColumns<R extends { error: PostgrestError | null }>(
    request: (photoColumns: string) => PromiseLike<R>,
  ): Promise<R> {
    const columns = this.storesWarnings ? PHOTO_COLUMNS : PHOTO_COLUMNS_WITHOUT_WARNINGS;
    const result = await request(columns);
    if (columns === PHOTO_COLUMNS_WITHOUT_WARNINGS || !isMissingWarningsColumn(result.error)) return result;
    this.noteMissingWarningsColumn();
    return request(PHOTO_COLUMNS_WITHOUT_WARNINGS);
  }

  async listVehicles(): Promise<VehicleWithPhotos[]> {
    const rows = new Map<string, VehicleListRow>();
    // Keyset paging in the list order (newest first, id breaks ties): every
    // page continues after the last vehicle of the previous one, so vehicles
    // created or deleted while paging never shift a page – none is skipped
    // or read twice.
    let last: VehicleListRow | undefined;
    for (;;) {
      const after = last;
      const { data, error, count } = await this.readPhotoColumns((photoColumns) => {
        let query = this.client
          .from("vehicles")
          .select(`${VEHICLE_COLUMNS}, vehicle_photos(${photoColumns})`, { count: "exact" })
          .is("vehicle_photos.archived_at", null)
          .order("created_at", { ascending: false })
          .order("id", { ascending: true })
          .limit(VEHICLE_PAGE_SIZE);
        if (after) {
          // Quoted: a timestamp contains characters PostgREST reserves (".", ":").
          const createdAt = JSON.stringify(after.created_at);
          query = query.or(`created_at.lt.${createdAt},and(created_at.eq.${createdAt},id.gt.${after.id})`);
        }
        return query;
      });
      if (error) throw toAppError(error);
      const page = (data ?? []) as unknown as VehicleListRow[];
      const known = rows.size;
      for (const row of page) if (!rows.has(row.id)) rows.set(row.id, row);
      // `count`: the vehicles from this page on. A page can be shorter than
      // asked for without being the last one (max-rows below the page size):
      // stop when it held all of them – or, without a count, at a page that
      // brings no new vehicle.
      const remaining = count !== null && Number.isFinite(count) ? count : Infinity;
      last = page.at(-1);
      if (!last || rows.size === known || page.length >= remaining) break;
    }

    return [...rows.values()].map((row) => ({
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

  async updateVehicle(vehicleId: string, input: VehicleInput, options: UpdateVehicleOptions = {}): Promise<Vehicle> {
    let query = this.client.from("vehicles").update(vehicleInputToRow(input)).eq("id", vehicleId);
    // Conditional update: only while the vehicle is unchanged since the
    // input's data was loaded (updated_at is set by a trigger on every change).
    if (options.expectedUpdatedAt) query = query.eq("updated_at", options.expectedUpdatedAt);
    const { data, error } = await query.select(VEHICLE_COLUMNS);
    if (error) {
      // Invalid UUIDs (22P02) simply mean "not found" for the UI.
      if (error.code === "22P02") throw new AppError("not_found", { cause: error });
      throw toAppError(error);
    }
    const [row] = (data ?? []) as VehicleRow[];
    if (row) return rowToVehicle(row);
    // Nothing updated: changed meanwhile – or not (or no longer) visible.
    if (options.expectedUpdatedAt && (await this.getVehicle(vehicleId))) throw new VehicleChangedError();
    throw new AppError("not_found");
  }

  async setVehicleStatus(vehicleId: string, status: VehicleStatus): Promise<void> {
    const { error } = await this.client.from("vehicles").update({ status }).eq("id", vehicleId);
    if (error) throw toAppError(error);
  }

  async listPhotos(vehicleId: string): Promise<VehiclePhoto[]> {
    const { data, error } = await this.readPhotoColumns((photoColumns) =>
      this.client.from("vehicle_photos").select(photoColumns).eq("vehicle_id", vehicleId).is("archived_at", null),
    );
    if (error) throw toAppError(error);
    return (data as unknown as VehiclePhotoRow[]).map(rowToPhoto);
  }

  private async signPaths(bucket: StorageBucket, paths: string[]): Promise<Map<string, string>> {
    const result = new Map<string, string>();
    const now = Date.now();
    const missing: string[] = [];
    for (const path of new Set(paths)) {
      const cached = this.signedUrlCache.get(`${bucket}/${path}`);
      if (cached && cached.expiresAt - now >= SIGNED_URL_MIN_REMAINING_MS) result.set(path, cached.url);
      else missing.push(path);
    }
    if (missing.length === 0) return result;

    const { data, error } = await this.client.storage
      .from(bucket)
      .createSignedUrls(missing, SIGNED_URL_TTL_SECONDS);
    if (error) throw toAppError(error);
    // Signed after `now`, so the real expiry is never earlier than this.
    const expiresAt = now + SIGNED_URL_TTL_SECONDS * 1000;
    for (const entry of data ?? []) {
      if (entry.path && entry.signedUrl) {
        result.set(entry.path, entry.signedUrl);
        this.signedUrlCache.set(`${bucket}/${entry.path}`, { url: entry.signedUrl, expiresAt });
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
    // storage-js sends a Blob as a multipart part with the Blob's own type and
    // ignores the contentType option – the bucket checks that type, so the
    // body itself must carry the normalised one ("image/jpg" → "image/jpeg").
    const contentType = normalizeImageMimeType(file.type);
    const body = file.type === contentType ? file : new Blob([file], { type: contentType });
    const { error } = await this.client.storage.from(bucket).upload(path, body, {
      contentType,
      upsert: options.upsert,
      cacheControl: "31536000",
    });
    if (!error) return;
    // A retry of the same capture: the original is already stored – fine.
    if (!options.upsert && isAlreadyExistsError(error)) return;
    throw toStorageAppError(error);
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
    // in one transaction; a capture older than the active photo is stored as
    // archived instead (see supabase/migrations).
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

  async saveProcessedPhoto({ photo, preset, file, warnings }: SaveProcessedPhotoInput): Promise<VehiclePhoto> {
    const path = buildProcessedStoragePath({
      vehicleId: photo.vehicleId,
      shotKey: photo.shotKey,
      photoId: photo.id,
      preset,
    });
    // Processed files are derivatives and may be regenerated (upsert).
    await this.upload(STORAGE_BUCKETS.processed, path, file, { upsert: true });
    await this.recordProcessedPhoto({ photoId: photo.id, preset, processedStoragePath: path, warnings });
    return {
      ...photo,
      processedStoragePath: path,
      processedPreset: preset,
      processingWarnings: parseProcessingWarnings(warnings),
    };
  }

  async recordProcessedPhoto(input: RecordProcessedPhotoInput): Promise<void> {
    const processed = { processed_storage_path: input.processedStoragePath, processed_preset: input.preset };
    const record = (values: Record<string, unknown>) =>
      this.client.from("vehicle_photos").update(values).eq("id", input.photoId);
    const withWarnings = this.storesWarnings;
    let { error } = withWarnings
      ? // The hints of this result replace those of an earlier processing –
        // bounded to what the column accepts.
        await record({ ...processed, processing_warnings: parseProcessingWarnings(input.warnings) })
      : await record(processed);
    if (withWarnings && isMissingWarningsColumn(error)) {
      this.noteMissingWarningsColumn();
      ({ error } = await record(processed));
    }
    if (error) throw toAppError(error);
    // A re-processed result reuses its path – make viewers fetch the new file.
    this.signedUrlCache.delete(`${STORAGE_BUCKETS.processed}/${input.processedStoragePath}`);
  }
}
