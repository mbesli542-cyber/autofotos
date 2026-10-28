/**
 * Offline-tolerant upload queue for captured photos.
 *
 * Every capture is first written to IndexedDB, then uploaded in the
 * background (one at a time, in capture order). If the upload fails – e.g.
 * weak workshop Wi-Fi – the photo stays on the device and is retried
 * automatically when the connection returns, or manually. A file the server
 * refuses for good (type, size) is not retried; the employee discards it.
 * Once a capture is saved, older captures of the same shot still waiting in
 * the queue are dropped: the newest capture stays the photo of the shot. A
 * refused file older than a photo saved elsewhere is dropped, too (the rule
 * of src/lib/photos/capture-order.ts).
 *
 * Kept deliberately simple: no background sync, no conflict resolution.
 * Retries are idempotent because the queue id is used as the photo id.
 */
import type { VehiclePhoto } from "@/lib/domain/types";
import { isRejectedFileError, toUserMessage } from "@/lib/errors";
import { prepareImage, type PreparedImage } from "@/lib/camera/image-utils";
import { createId } from "@/lib/ids";
import { isOlderCapture, isOlderThanSavedPhoto } from "@/lib/photos/capture-order";
import { IdbDatabase } from "./idb";

export interface PendingUploadRecord {
  id: string;
  vehicleId: string;
  shotKey: string;
  shotOrder: number;
  title: string;
  file: Blob;
  takenAt: string;
  attempts: number;
  lastError: string | null;
  /** false: the server refused the file for good (missing in older records). */
  retryable?: boolean;
  createdAt: string;
}

export type NewPendingUpload = Pick<
  PendingUploadRecord,
  "vehicleId" | "shotKey" | "shotOrder" | "title" | "file" | "takenAt"
>;

export type UploadItemStatus = "queued" | "uploading" | "failed";

export interface UploadItem {
  id: string;
  vehicleId: string;
  shotKey: string;
  shotOrder: number;
  title: string;
  /** Capture time (ISO). A newer saved photo of the shot wins over this item. */
  takenAt: string;
  status: UploadItemStatus;
  /** Local preview (object URL of the thumbnail). */
  thumbnailUrl: string | null;
  error: string | null;
  /**
   * false when the server refused the file for good (unsupported type, too
   * large): it is not retried automatically and can only be discarded.
   */
  retryable: boolean;
}

export interface UploadQueueSnapshot {
  items: readonly UploadItem[];
}

export type UploadProcessor = (
  record: PendingUploadRecord,
  prepared: PreparedImage,
) => Promise<void>;

const STORE = "pendingUploads";
const EMPTY_SNAPSHOT: UploadQueueSnapshot = { items: [] };

function shotSlotKey(record: Pick<PendingUploadRecord, "vehicleId" | "shotKey">): string {
  return `${record.vehicleId}/${record.shotKey}`;
}

/** The queue's own IndexedDB database (separate from the demo data). */
export function createUploadQueueDatabase(): IdbDatabase {
  return new IdbDatabase("autoexperten-photo-local", 1, [
    { name: STORE, keyPath: "id", indexes: [{ name: "vehicleId", keyPath: "vehicleId" }] },
  ]);
}

export class UploadQueue {
  private readonly records = new Map<string, PendingUploadRecord>();
  private readonly prepared = new Map<string, Promise<PreparedImage>>();
  private items: UploadItem[] = [];
  private snapshot: UploadQueueSnapshot = EMPTY_SNAPSHOT;
  private readonly listeners = new Set<() => void>();
  private readonly uploadedListeners = new Set<(item: UploadItem) => void>();
  /**
   * Capture time of the newest capture saved in this session, per vehicle +
   * shot. Compared with the raw capture times of the other captures of this
   * device (one clock): a capture taken before it never replaces it, even
   * when the database would count the saved one as older (clock ahead).
   */
  private readonly savedTakenAt = new Map<string, string>();
  /** Captures kept in memory only (IndexedDB refused to store them). */
  private readonly unpersisted = new Set<string>();
  /**
   * Captures being discarded ("Verwerfen"): their stored record is being
   * deleted. Meanwhile they are neither uploaded nor retried.
   */
  private readonly discarding = new Map<string, Promise<void>>();
  private running = false;
  private runPromise: Promise<void> = Promise.resolve();
  private restorePromise: Promise<void> | null = null;

  constructor(
    private readonly processor: UploadProcessor,
    private readonly thumbnailMaxEdge: number,
    private readonly db: IdbDatabase = createUploadQueueDatabase(),
  ) {}

  /* ---------- useSyncExternalStore API ---------- */

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): UploadQueueSnapshot => this.snapshot;

  getServerSnapshot = (): UploadQueueSnapshot => EMPTY_SNAPSHOT;

  /** Called after each successful upload (pages reload their data). */
  onUploaded(listener: (item: UploadItem) => void): () => void {
    this.uploadedListeners.add(listener);
    return () => this.uploadedListeners.delete(listener);
  }

  /* ---------- lifecycle ---------- */

  /** Restores uploads left over from a previous session and resumes them. */
  restore(): Promise<void> {
    this.restorePromise ??= (async () => {
      let stored: PendingUploadRecord[] = [];
      try {
        stored = await this.db.getAll<PendingUploadRecord>(STORE);
      } catch {
        stored = [];
      }
      stored.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
      for (const record of stored) {
        if (this.records.has(record.id)) continue;
        if (this.isSuperseded(record)) {
          // A newer capture of this shot was saved meanwhile.
          void this.db.delete(STORE, record.id).catch(() => undefined);
          continue;
        }
        this.records.set(record.id, record);
        // A file the server refused for good is not uploaded again.
        const status = record.retryable === false ? "failed" : "queued";
        this.items = [...this.items, this.toItem(record, status)];
        void this.ensurePrepared(record);
      }
      this.emit();
      if (typeof window !== "undefined") {
        window.addEventListener("online", () => this.retryAll());
      }
      void this.run();
    })();
    return this.restorePromise;
  }

  async enqueue(input: NewPendingUpload): Promise<UploadItem> {
    const record: PendingUploadRecord = {
      ...input,
      id: createId(),
      attempts: 0,
      lastError: null,
      createdAt: new Date().toISOString(),
    };
    this.records.set(record.id, record);
    try {
      await this.db.put(STORE, record);
    } catch {
      // Storage full/blocked: keep it in memory for this session.
      this.unpersisted.add(record.id);
    }
    const item = this.toItem(record, "queued");
    this.items = [...this.items, item];
    this.emit();
    void this.ensurePrepared(record);
    void this.run();
    return item;
  }

  /** Explicit retry of one failed capture (also of a file refused before). */
  retry(id: string): void {
    if (this.discarding.has(id)) return;
    if (this.items.find((item) => item.id === id)?.status !== "failed") return;
    this.updateItem(id, { status: "queued", error: null });
    void this.run();
  }

  /** Retries every failed upload that can still succeed (connection back, "Erneut versuchen"). */
  retryAll(): void {
    let changed = false;
    this.items = this.items.map((item) => {
      if (item.status !== "failed" || !item.retryable || this.discarding.has(item.id)) return item;
      changed = true;
      return { ...item, status: "queued", error: null };
    });
    if (changed) {
      this.emit();
      void this.run();
    }
  }

  /**
   * Removes a capture that was not saved (user decision "Verwerfen"): its
   * persisted record first, then the local copy and its preview. Until then
   * it is neither uploaded nor retried ("online", "Erneut versuchen"). If the
   * record cannot be deleted, the error is passed on and the capture stays –
   * it would otherwise come back (and be uploaded) at the next start. An
   * upload in progress is not interrupted.
   */
  discard(id: string): Promise<void> {
    const running = this.discarding.get(id);
    if (running) return running;
    if (this.items.find((item) => item.id === id)?.status === "uploading") return Promise.resolve();
    const deleted = this.unpersisted.has(id) ? Promise.resolve() : this.db.delete(STORE, id);
    const discarding = deleted.then(
      () => {
        this.discarding.delete(id);
        this.forget(id);
      },
      (error: unknown) => {
        this.discarding.delete(id);
        // It stays: a capture that waited meanwhile is uploaded after all.
        void this.run();
        throw error;
      },
    );
    this.discarding.set(id, discarding);
    return discarding;
  }

  /**
   * Drops refused captures (retryable=false) that are older than the saved
   * photo of their shot, e.g. a retake saved on another device: that photo
   * hides them in the slot, so they could neither be saved nor discarded.
   * Called with the photos a page loaded.
   */
  async dropRefusedOlderThan(
    photos: readonly Pick<VehiclePhoto, "vehicleId" | "shotKey" | "takenAt" | "createdAt">[],
  ): Promise<void> {
    // Captures left over from an earlier session are known once restored.
    await this.restorePromise;
    const saved = new Map(photos.map((photo) => [shotSlotKey(photo), photo]));
    const hidden = this.items.filter((item) => {
      if (item.status !== "failed" || item.retryable) return false;
      const photo = saved.get(shotSlotKey(item));
      // The same rule as the photo slots: hidden behind the saved photo.
      return photo !== undefined && isOlderThanSavedPhoto(item.takenAt, photo);
    });
    await Promise.all(hidden.map((item) => this.remove(item.id)));
  }

  /**
   * Removes every capture (demo reset): records, previews and persisted
   * copies. Resolves once an upload in progress has finished, so nothing is
   * written afterwards.
   */
  async clear(): Promise<void> {
    for (const item of this.items) {
      if (item.thumbnailUrl) URL.revokeObjectURL(item.thumbnailUrl);
    }
    this.records.clear();
    this.prepared.clear();
    this.savedTakenAt.clear();
    this.unpersisted.clear();
    this.items = [];
    this.emit();
    await this.runPromise.catch(() => undefined);
    await this.db.clear([STORE]);
  }

  /* ---------- internals ---------- */

  private toItem(record: PendingUploadRecord, status: UploadItemStatus): UploadItem {
    return {
      id: record.id,
      vehicleId: record.vehicleId,
      shotKey: record.shotKey,
      shotOrder: record.shotOrder,
      title: record.title,
      takenAt: record.takenAt,
      status,
      thumbnailUrl: null,
      error: record.lastError,
      retryable: record.retryable !== false,
    };
  }

  /** True if a newer capture of the same shot was saved in this session. */
  private isSuperseded(record: PendingUploadRecord): boolean {
    const saved = this.savedTakenAt.get(shotSlotKey(record));
    return saved !== undefined && isOlderCapture(record.takenAt, saved);
  }

  /**
   * After a capture was saved, older captures of the same shot that still
   * wait (queued or failed) are dropped: uploaded later they could only
   * become archived copies.
   */
  private async dropOlderCaptures(saved: PendingUploadRecord): Promise<void> {
    const key = shotSlotKey(saved);
    const newest = this.savedTakenAt.get(key);
    if (newest === undefined || isOlderCapture(newest, saved.takenAt)) {
      this.savedTakenAt.set(key, saved.takenAt);
    }
    const obsolete = [...this.records.values()].filter((record) => this.isSuperseded(record));
    await Promise.all(obsolete.map((record) => this.remove(record.id)));
  }

  private ensurePrepared(record: PendingUploadRecord): Promise<PreparedImage> {
    let promise = this.prepared.get(record.id);
    if (!promise) {
      promise = prepareImage(record.file, this.thumbnailMaxEdge).then((prepared) => {
        if (this.records.has(record.id)) {
          const preview = prepared.thumbnail ?? record.file;
          this.updateItem(record.id, { thumbnailUrl: URL.createObjectURL(preview) });
        }
        return prepared;
      });
      this.prepared.set(record.id, promise);
    }
    return promise;
  }

  private run(): Promise<void> {
    if (!this.running) this.runPromise = this.drain();
    return this.runPromise;
  }

  private async drain(): Promise<void> {
    this.running = true;
    try {
      for (;;) {
        const next = this.items.find((item) => item.status === "queued" && !this.discarding.has(item.id));
        if (!next) break;
        const record = this.records.get(next.id);
        if (!record) {
          this.items = this.items.filter((item) => item.id !== next.id);
          this.emit();
          continue;
        }
        this.updateItem(record.id, { status: "uploading", error: null });
        try {
          const prepared = await this.ensurePrepared(record);
          await this.processor(record, prepared);
          const done = this.items.find((item) => item.id === record.id);
          await this.remove(record.id);
          if (done) {
            await this.dropOlderCaptures(record);
            for (const listener of this.uploadedListeners) listener(done);
          }
        } catch (error) {
          // Removed meanwhile (saved, or the queue was cleared): nothing to keep.
          if (!this.records.has(record.id)) continue;
          const retryable = !isRejectedFileError(error);
          const message = toUserMessage(
            error,
            "Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
          );
          const updated = { ...record, attempts: record.attempts + 1, lastError: message, retryable };
          this.records.set(record.id, updated);
          try {
            await this.db.put(STORE, updated);
          } catch {
            // keep in memory
          }
          this.updateItem(record.id, { status: "failed", error: message, retryable });
        }
      }
    } finally {
      this.running = false;
    }
  }

  /** Drops a capture from this session (memory, preview) – not its persisted record. */
  private forget(id: string): void {
    const item = this.items.find((entry) => entry.id === id);
    if (item?.thumbnailUrl) URL.revokeObjectURL(item.thumbnailUrl);
    this.records.delete(id);
    this.prepared.delete(id);
    this.unpersisted.delete(id);
    this.items = this.items.filter((entry) => entry.id !== id);
    this.emit();
  }

  /** Removes a saved or superseded capture; its persisted record is deleted best-effort. */
  private async remove(id: string): Promise<void> {
    this.forget(id);
    try {
      await this.db.delete(STORE, id);
    } catch {
      // Ignored: a leftover record is sent again at the next start – harmless,
      // a photo id is saved only once and an older capture is stored archived.
    }
  }

  private updateItem(id: string, patch: Partial<UploadItem>): void {
    let changed = false;
    this.items = this.items.map((item) => {
      if (item.id !== id) return item;
      changed = true;
      return { ...item, ...patch };
    });
    if (changed) this.emit();
  }

  private emit(): void {
    this.snapshot = { items: this.items };
    for (const listener of this.listeners) listener();
  }
}
