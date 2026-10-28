/**
 * Which picked photo files ("Foto hochladen", Zusatzfotos) the app accepts
 * (pure): the formats and the size limit of the storage for originals
 * (`vehicle-originals` bucket, supabase/migrations). Checked before anything
 * is queued – in demo mode too – so a file that can never be saved is not
 * kept on the device for endless retries.
 */
import { USER_ERROR_MESSAGES } from "@/lib/errors";

/** Accepted MIME types (the formats of the originals storage). */
export const PHOTO_FILE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"] as const;

export type PhotoFileType = (typeof PHOTO_FILE_TYPES)[number];

/**
 * `accept` attribute for photo file inputs – without HEIC/HEIF: iOS Safari
 * then converts photos from the library to JPEG, which every browser can
 * show (listing HEIC makes it pass the HEIC original through, which Chrome,
 * Firefox and Android cannot display). A HEIC file picked anyway (e.g. from
 * the files app) is still accepted by checkPhotoFile.
 */
export const PHOTO_FILE_ACCEPT = PHOTO_FILE_TYPES.filter((type) => type !== "image/heic" && type !== "image/heif").join(
  ",",
);

/** 50 MB – the file size limit of the originals bucket. */
export const PHOTO_FILE_MAX_BYTES = 50 * 1024 * 1024;

const TYPE_BY_EXTENSION: Record<string, PhotoFileType> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
  heif: "image/heif",
};

/** Non-standard spellings some browsers and apps report. */
const TYPE_ALIASES: Record<string, PhotoFileType> = {
  "image/jpg": "image/jpeg",
  "image/pjpeg": "image/jpeg",
};

/** Types browsers report when they do not know the format (e.g. HEIC on Windows). */
const UNKNOWN_TYPES = new Set(["", "application/octet-stream"]);

function isPhotoFileType(type: string): type is PhotoFileType {
  return (PHOTO_FILE_TYPES as readonly string[]).includes(type);
}

/** The accepted type of a file, or null: "image/jpg" counts as JPEG, an unknown type is taken from the extension. */
export function getPhotoFileType(file: { name: string; type: string }): PhotoFileType | null {
  const type = file.type.trim().toLowerCase();
  const alias = TYPE_ALIASES[type];
  if (alias) return alias;
  if (isPhotoFileType(type)) return type;
  if (!UNKNOWN_TYPES.has(type)) return null;
  const extension = /\.([a-z0-9]+)$/i.exec(file.name)?.[1]?.toLowerCase() ?? "";
  return TYPE_BY_EXTENSION[extension] ?? null;
}

export type PhotoFileCheck = { ok: true; file: File } | { ok: false; message: string };

/**
 * Accepts a picked file or explains (in German) why not. An accepted file is
 * returned with its accepted MIME type – the upload sends `file.type` to the
 * storage – and otherwise unchanged (same bytes, name and date).
 */
export function checkPhotoFile(file: File): PhotoFileCheck {
  const type = getPhotoFileType(file);
  if (!type) return { ok: false, message: USER_ERROR_MESSAGES.unsupported_file };
  if (file.size > PHOTO_FILE_MAX_BYTES) return { ok: false, message: USER_ERROR_MESSAGES.file_too_large };
  if (file.type === type) return { ok: true, file };
  return { ok: true, file: new File([file], file.name, { type, lastModified: file.lastModified }) };
}
