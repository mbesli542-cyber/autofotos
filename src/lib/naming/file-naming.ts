/**
 * Deterministic names for storage objects and exported listing photos.
 *
 * Storage layout (Supabase Storage buckets):
 *   vehicle-originals/{vehicleId}/{shotKey}/{photoId}.{ext}
 *   vehicle-thumbnails/{vehicleId}/{shotKey}/{photoId}.jpg
 *   vehicle-processed/{vehicleId}/{preset}/{shotKey}/{photoId}.jpg
 *
 * Every capture gets its own photoId, so a retake can never overwrite an
 * existing original.
 */

export const STORAGE_BUCKETS = {
  originals: "vehicle-originals",
  thumbnails: "vehicle-thumbnails",
  processed: "vehicle-processed",
} as const;

export type StorageBucket = (typeof STORAGE_BUCKETS)[keyof typeof STORAGE_BUCKETS];

const MIME_EXTENSIONS: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/heic": "heic",
  "image/heif": "heif",
  "image/svg+xml": "svg",
};

export function getFileExtensionForMimeType(mimeType: string): string {
  return MIME_EXTENSIONS[mimeType.toLowerCase()] ?? "jpg";
}

const TRANSLITERATIONS: Record<string, string> = {
  ä: "ae",
  ö: "oe",
  ü: "ue",
  Ä: "Ae",
  Ö: "Oe",
  Ü: "Ue",
  ß: "ss",
};

/** Safe file name segment: ASCII letters, digits and dashes only. */
export function sanitizeFileNameSegment(value: string, maxLength = 40): string {
  return value
    .replace(/[äöüÄÖÜß]/g, (char) => TRANSLITERATIONS[char] ?? char)
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, maxLength)
    .replace(/-$/, "");
}

export interface ExportFileNameInput {
  internalReference: string | null;
  vehicleId: string;
  shotOrder: number;
  shotKey: string;
  extension?: string;
}

/**
 * Listing export name, e.g. `AE_FZ-2041_01_front_left_45.jpg`.
 * Falls back to the first 8 characters of the vehicle id when no internal
 * reference was entered.
 */
export function buildExportFileName({
  internalReference,
  vehicleId,
  shotOrder,
  shotKey,
  extension = "jpg",
}: ExportFileNameInput): string {
  const reference =
    sanitizeFileNameSegment(internalReference ?? "") ||
    sanitizeFileNameSegment(vehicleId.replace(/-/g, "").slice(0, 8).toUpperCase());
  const order = String(shotOrder).padStart(2, "0");
  const key = shotKey.replace(/[^a-z0-9_]/gi, "_");
  return `AE_${reference}_${order}_${key}.${extension.replace(/^\./, "")}`;
}

interface PhotoPathInput {
  vehicleId: string;
  shotKey: string;
  photoId: string;
}

export function buildOriginalStoragePath({
  vehicleId,
  shotKey,
  photoId,
  extension,
}: PhotoPathInput & { extension: string }): string {
  return `${vehicleId}/${shotKey}/${photoId}.${extension}`;
}

export function buildThumbnailStoragePath({
  vehicleId,
  shotKey,
  photoId,
}: PhotoPathInput): string {
  return `${vehicleId}/${shotKey}/${photoId}.jpg`;
}

export function buildProcessedStoragePath({
  vehicleId,
  shotKey,
  photoId,
  preset,
}: PhotoPathInput & { preset: string }): string {
  return `${vehicleId}/${preset}/${shotKey}/${photoId}.jpg`;
}
