/**
 * Dependency-free ZIP writer for the photo export (pure, store-only).
 *
 * JPEGs do not compress, so every entry is STORED (method 0): local file
 * header + the file's bytes, then the central directory and its end record.
 * Names are UTF-8 (general purpose bit 11). Sizes and CRC-32 are known before
 * writing, so no data descriptors are needed.
 *
 * The archive is planned from the entries' sizes and CRCs alone
 * (`planStoredZip`): the file bytes never have to sit in one buffer – the
 * browser joins the planned headers and the photo Blobs into one Blob
 * (zip-blob.ts). ZIP64 is not written: an archive that would need it (4 GB
 * or more, more than 65 535 entries) is refused with a ZipError.
 */

/** Largest value a 32-bit ZIP field may hold without signalling ZIP64 (0xFFFFFFFF). */
export const ZIP_MAX_32 = 0xffff_fffe;
/** Largest entry count without ZIP64 (0xFFFF signals ZIP64). */
export const ZIP_MAX_ENTRIES = 0xfffe;

const LOCAL_HEADER_SIZE = 30;
const CENTRAL_HEADER_SIZE = 46;
const END_RECORD_SIZE = 22;
/** Version 1.0: stored entries, no ZIP64. */
const VERSION_NEEDED = 10;
/** Made by MS-DOS (0), spec version 2.0. */
const VERSION_MADE_BY = 20;
/** General purpose bit 11: file name is UTF-8. */
const FLAG_UTF8 = 0x0800;
const METHOD_STORED = 0;

export type ZipErrorCode = "too_large" | "too_many_entries" | "invalid_name" | "duplicate_name" | "invalid_entry";

export class ZipError extends Error {
  readonly code: ZipErrorCode;

  constructor(code: ZipErrorCode, message: string) {
    super(message);
    this.name = "ZipError";
    this.code = code;
  }
}

/* ------------------------------------------------------------------------ */
/* CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320)                      */
/* ------------------------------------------------------------------------ */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb8_8320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

/**
 * CRC-32 of `data`. Incremental: pass the CRC of the bytes before as
 * `previous` (crc32(b, crc32(a)) === crc32(a + b)).
 */
export function crc32(data: Uint8Array, previous = 0): number {
  let crc = (previous ^ 0xffff_ffff) >>> 0;
  for (let i = 0; i < data.length; i += 1) {
    crc = (CRC_TABLE[(crc ^ (data[i] ?? 0)) & 0xff] ?? 0) ^ (crc >>> 8);
  }
  return (crc ^ 0xffff_ffff) >>> 0;
}

/* ------------------------------------------------------------------------ */
/* Archive plan                                                              */
/* ------------------------------------------------------------------------ */

export interface ZipEntryMeta {
  /** Path inside the archive ("/" separated, relative, no ".." segments). */
  name: string;
  /** Number of bytes of the entry. */
  size: number;
  /** CRC-32 of the entry's bytes. */
  crc32: number;
  /** Modification time (stored as local MS-DOS time, 1980–2107, 2 s steps). */
  modified: Date;
}

/** A planned piece of the archive: header bytes, or the bytes of entry `entry`. */
export type ZipPart = Uint8Array | { entry: number };

export interface ZipPlan {
  parts: ZipPart[];
  /** Total archive size in bytes. */
  size: number;
}

const encoder = new TextEncoder();

/** MS-DOS date and time (local time, clamped to 1980-01-01 … 2107-12-31). */
export function toDosDateTime(value: Date): { date: number; time: number } {
  const valid = Number.isNaN(value.getTime()) ? new Date(1980, 0, 1) : value;
  const year = valid.getFullYear();
  if (year < 1980) return { date: (1 << 5) | 1, time: 0 };
  if (year > 2107) return { date: (127 << 9) | (12 << 5) | 31, time: (23 << 11) | (59 << 5) | 29 };
  return {
    date: ((year - 1980) << 9) | ((valid.getMonth() + 1) << 5) | valid.getDate(),
    time: (valid.getHours() << 11) | (valid.getMinutes() << 5) | Math.floor(valid.getSeconds() / 2),
  };
}

function checkName(name: string): Uint8Array {
  const segments = name.split("/");
  const invalid =
    name.length === 0 ||
    name.startsWith("/") ||
    name.endsWith("/") ||
    name.includes("\\") ||
    /[\u0000-\u001f]/.test(name) ||
    segments.some((segment) => segment === "" || segment === "." || segment === "..");
  if (invalid) throw new ZipError("invalid_name", `Invalid ZIP entry name: ${JSON.stringify(name)}`);
  const bytes = encoder.encode(name);
  if (bytes.length > 0xffff) throw new ZipError("invalid_name", "ZIP entry name too long");
  return bytes;
}

function localHeader(meta: ZipEntryMeta, name: Uint8Array): Uint8Array {
  const { date, time } = toDosDateTime(meta.modified);
  const header = new Uint8Array(LOCAL_HEADER_SIZE + name.length);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x0403_4b50, true);
  view.setUint16(4, VERSION_NEEDED, true);
  view.setUint16(6, FLAG_UTF8, true);
  view.setUint16(8, METHOD_STORED, true);
  view.setUint16(10, time, true);
  view.setUint16(12, date, true);
  view.setUint32(14, meta.crc32 >>> 0, true);
  view.setUint32(18, meta.size, true); // compressed size (stored)
  view.setUint32(22, meta.size, true); // uncompressed size
  view.setUint16(26, name.length, true);
  view.setUint16(28, 0, true); // extra field length
  header.set(name, LOCAL_HEADER_SIZE);
  return header;
}

function centralHeader(meta: ZipEntryMeta, name: Uint8Array, offset: number): Uint8Array {
  const { date, time } = toDosDateTime(meta.modified);
  const header = new Uint8Array(CENTRAL_HEADER_SIZE + name.length);
  const view = new DataView(header.buffer);
  view.setUint32(0, 0x0201_4b50, true);
  view.setUint16(4, VERSION_MADE_BY, true);
  view.setUint16(6, VERSION_NEEDED, true);
  view.setUint16(8, FLAG_UTF8, true);
  view.setUint16(10, METHOD_STORED, true);
  view.setUint16(12, time, true);
  view.setUint16(14, date, true);
  view.setUint32(16, meta.crc32 >>> 0, true);
  view.setUint32(20, meta.size, true);
  view.setUint32(24, meta.size, true);
  view.setUint16(28, name.length, true);
  view.setUint16(30, 0, true); // extra field length
  view.setUint16(32, 0, true); // comment length
  view.setUint16(34, 0, true); // disk number start
  view.setUint16(36, 0, true); // internal attributes
  view.setUint32(38, 0, true); // external attributes
  view.setUint32(42, offset, true);
  header.set(name, CENTRAL_HEADER_SIZE);
  return header;
}

function endRecord(entries: number, centralSize: number, centralOffset: number): Uint8Array {
  const record = new Uint8Array(END_RECORD_SIZE);
  const view = new DataView(record.buffer);
  view.setUint32(0, 0x0605_4b50, true);
  view.setUint16(4, 0, true); // this disk
  view.setUint16(6, 0, true); // disk with the central directory
  view.setUint16(8, entries, true);
  view.setUint16(10, entries, true);
  view.setUint32(12, centralSize, true);
  view.setUint32(16, centralOffset, true);
  view.setUint16(20, 0, true); // comment length
  return record;
}

const TOO_LARGE = "ZIP archive would need ZIP64 (4 GB or more)";

/**
 * The pieces of a store-only archive for these entries, in order: for every
 * entry its local header followed by `{ entry: index }` (where its bytes go),
 * then the central directory and the end record. Throws a ZipError for an
 * invalid or duplicate name (compared case-insensitively – the archive is
 * unpacked on Windows/macOS) and for archives that would need ZIP64.
 */
export function planStoredZip(entries: readonly ZipEntryMeta[]): ZipPlan {
  if (entries.length > ZIP_MAX_ENTRIES) throw new ZipError("too_many_entries", "Too many ZIP entries");
  const seen = new Set<string>();
  const parts: ZipPart[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  entries.forEach((meta, index) => {
    if (!Number.isSafeInteger(meta.size) || meta.size < 0) {
      throw new ZipError("invalid_entry", `Invalid size of ZIP entry ${index}`);
    }
    if (!Number.isInteger(meta.crc32) || meta.crc32 < 0 || meta.crc32 > 0xffff_ffff) {
      throw new ZipError("invalid_entry", `Invalid CRC-32 of ZIP entry ${index}`);
    }
    const name = checkName(meta.name);
    const folded = meta.name.toLowerCase();
    if (seen.has(folded)) throw new ZipError("duplicate_name", `Duplicate ZIP entry name: ${meta.name}`);
    seen.add(folded);
    if (meta.size > ZIP_MAX_32 || offset > ZIP_MAX_32) throw new ZipError("too_large", TOO_LARGE);

    const header = localHeader(meta, name);
    central.push(centralHeader(meta, name, offset));
    parts.push(header, { entry: index });
    offset += header.length + meta.size;
  });

  const centralOffset = offset;
  const centralSize = central.reduce((sum, header) => sum + header.length, 0);
  if (centralOffset > ZIP_MAX_32 || centralSize > ZIP_MAX_32 || centralOffset + centralSize > ZIP_MAX_32) {
    throw new ZipError("too_large", TOO_LARGE);
  }
  parts.push(...central, endRecord(entries.length, centralSize, centralOffset));
  return { parts, size: centralOffset + centralSize + END_RECORD_SIZE };
}

export interface ZipFileInput {
  name: string;
  data: Uint8Array;
  /** Default: now. */
  modified?: Date;
}

/** A complete store-only archive in memory (small archives and tests; the export uses zip-blob.ts). */
export function createStoredZip(files: readonly ZipFileInput[]): Uint8Array {
  const now = new Date();
  const plan = planStoredZip(
    files.map((file) => ({ name: file.name, size: file.data.length, crc32: crc32(file.data), modified: file.modified ?? now })),
  );
  const archive = new Uint8Array(plan.size);
  let position = 0;
  for (const part of plan.parts) {
    const bytes = part instanceof Uint8Array ? part : (files[part.entry]?.data ?? new Uint8Array());
    archive.set(bytes, position);
    position += bytes.length;
  }
  return archive;
}
