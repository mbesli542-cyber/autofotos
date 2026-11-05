/**
 * The photo files of an export: loaded from the data backend
 * (DataProvider.getPhotoFile – IndexedDB in demo mode, Storage with the
 * session in Supabase mode), named AE_{ref}_{nn}_{shot}.{ext}, and – for the
 * ZIP – their CRC-32.
 *
 * ExportFileLoader loads them in the background as soon as the summary opens
 * (a few at a time), so the share sheet can open straight from the next tap.
 * It is an external store (subscribe/getSnapshot) for useSyncExternalStore.
 */
import type { DataProvider } from "@/lib/data/types";
import { AppError, toUserMessage } from "@/lib/errors";
import { getStoragePathExtension, normalizeImageMimeType } from "@/lib/naming/file-naming";
import type { ExportItem } from "./listing-export";
import { buildZipBlob, crc32OfBlob } from "./zip-blob";

/** Files loaded at the same time. */
export const EXPORT_LOAD_CONCURRENCY = 3;

const MIME_BY_EXTENSION: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  heic: "image/heic",
  heif: "image/heif",
  svg: "image/svg+xml",
};

/** Identifies an item's file: the photo, its version and the name it gets. */
export function exportFileKey(item: Pick<ExportItem, "photo" | "version" | "fileName">): string {
  return `${item.photo?.id ?? ""}:${item.version ?? ""}:${item.fileName ?? ""}`;
}

/** The item's file as a File named for the export (processed results are JPEGs). */
export async function loadExportFile(
  data: DataProvider,
  item: Pick<ExportItem, "photo" | "version" | "fileName">,
): Promise<File> {
  const { photo, version, fileName } = item;
  if (!photo || !version || !fileName) throw new AppError("not_found");
  const blob = await data.getPhotoFile(photo, version);
  const extension = getStoragePathExtension(fileName) ?? "jpg";
  const type =
    version === "processed"
      ? "image/jpeg"
      : blob.type
        ? normalizeImageMimeType(blob.type)
        : (MIME_BY_EXTENSION[extension] ?? "image/jpeg");
  const takenAt = Date.parse(photo.takenAt);
  return new File([blob], fileName, { type, lastModified: Number.isNaN(takenAt) ? Date.now() : takenAt });
}

/** A ZIP of these files (in this order), named by the files' own names. */
export async function buildExportZip(files: readonly File[], crcs: (file: File) => Promise<number>): Promise<Blob> {
  const entries = [];
  for (const file of files) {
    entries.push({ name: file.name, blob: file, crc32: await crcs(file), modified: new Date(file.lastModified) });
  }
  return buildZipBlob(entries);
}

export interface ExportFilesSnapshot {
  /** Loaded files by exportFileKey. */
  files: ReadonlyMap<string, File>;
  /** German reason by exportFileKey for files that could not be loaded. */
  errors: ReadonlyMap<string, string>;
  /** Files being loaded or waiting to be loaded. */
  loading: number;
}

const EMPTY: ExportFilesSnapshot = { files: new Map(), errors: new Map(), loading: 0 };

export class ExportFileLoader {
  private files = new Map<string, File>();
  private errors = new Map<string, string>();
  private queue: { key: string; item: ExportItem }[] = [];
  private active = new Set<string>();
  private crcs = new WeakMap<File, Promise<number>>();
  private generation = 0;
  private snapshot: ExportFilesSnapshot = EMPTY;
  private listeners = new Set<() => void>();

  constructor(
    /** Called when a file is loaded (browser only – getAppServices() must not run during render). */
    private readonly getData: () => DataProvider,
    private readonly concurrency = EXPORT_LOAD_CONCURRENCY,
  ) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): ExportFilesSnapshot => this.snapshot;

  private emit(): void {
    this.snapshot = { files: new Map(this.files), errors: new Map(this.errors), loading: this.queue.length + this.active.size };
    for (const listener of this.listeners) listener();
  }

  /**
   * Loads the files of these items that are neither loaded nor on their way.
   * A file that failed is only tried again with `retry`.
   */
  want(items: readonly ExportItem[], { retry = false } = {}): void {
    let changed = false;
    for (const item of items) {
      if (!item.photo || !item.version || !item.fileName) continue;
      const key = exportFileKey(item);
      if (this.files.has(key) || this.active.has(key) || this.queue.some((entry) => entry.key === key)) continue;
      if (this.errors.has(key)) {
        if (!retry) continue;
        this.errors.delete(key);
      }
      this.queue.push({ key, item });
      changed = true;
    }
    if (!changed) return;
    this.pump();
    this.emit();
  }

  private pump(): void {
    while (this.active.size < this.concurrency && this.queue.length > 0) {
      const next = this.queue.shift();
      if (!next) break;
      this.active.add(next.key);
      const generation = this.generation;
      void loadExportFile(this.getData(), next.item).then(
        (file) => this.finish(generation, next.key, () => this.files.set(next.key, file)),
        (error: unknown) =>
          this.finish(generation, next.key, () =>
            this.errors.set(next.key, toUserMessage(error, "Das Foto konnte nicht geladen werden.")),
          ),
      );
    }
  }

  private finish(generation: number, key: string, record: () => void): void {
    if (generation !== this.generation) return; // cleared meanwhile
    this.active.delete(key);
    record();
    this.pump();
    this.emit();
  }

  /** The CRC-32 of a loaded file (computed once). */
  crcOf = (file: File): Promise<number> => {
    let crc = this.crcs.get(file);
    if (!crc) {
      crc = crc32OfBlob(file);
      this.crcs.set(file, crc);
    }
    return crc;
  };

  /** Forgets every file (frees the memory) and drops answers still on their way. */
  clear(): void {
    this.generation += 1;
    this.files.clear();
    this.errors.clear();
    this.queue = [];
    this.active.clear();
    this.crcs = new WeakMap();
    if (this.snapshot !== EMPTY) {
      this.snapshot = EMPTY;
      for (const listener of this.listeners) listener();
    }
  }
}
