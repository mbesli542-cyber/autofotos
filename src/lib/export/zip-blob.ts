/**
 * The photo export's ZIP file in the browser: the planned headers of zip.ts
 * joined with the photo Blobs into one Blob (no copy of all photos in one
 * buffer – the browser keeps large Blobs on disk where it can).
 */
import { crc32, planStoredZip, type ZipEntryMeta } from "./zip";

/** Bytes read at a time for the CRC (keeps memory flat for large originals). */
const CRC_CHUNK_BYTES = 4 * 1024 * 1024;

/** CRC-32 of a Blob, read in chunks. */
export async function crc32OfBlob(blob: Blob, signal?: AbortSignal): Promise<number> {
  let crc = 0;
  for (let start = 0; start < blob.size; start += CRC_CHUNK_BYTES) {
    signal?.throwIfAborted();
    const chunk = new Uint8Array(await blob.slice(start, start + CRC_CHUNK_BYTES).arrayBuffer());
    crc = crc32(chunk, crc);
  }
  return crc;
}

export interface ZipBlobEntry {
  name: string;
  blob: Blob;
  /** CRC-32 of the blob (crc32OfBlob). */
  crc32: number;
  modified: Date;
}

/** The archive as one Blob ("application/zip"). Throws a ZipError like planStoredZip. */
export function buildZipBlob(entries: readonly ZipBlobEntry[]): Blob {
  const metas: ZipEntryMeta[] = entries.map((entry) => ({
    name: entry.name,
    size: entry.blob.size,
    crc32: entry.crc32,
    modified: entry.modified,
  }));
  const { parts } = planStoredZip(metas);
  const blobParts: BlobPart[] = parts.map((part) =>
    part instanceof Uint8Array ? (part as Uint8Array<ArrayBuffer>) : (entries[part.entry]?.blob ?? new Blob()),
  );
  return new Blob(blobParts, { type: "application/zip" });
}
