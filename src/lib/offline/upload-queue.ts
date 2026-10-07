/**
 * Offline-tolerant upload queue for captured photos.
 *
 * Every capture is first written to IndexedDB, then uploaded in the
 * background (one at a time, in capture order). If the upload fails – e.g.
 * weak workshop Wi-Fi – the photo stays on the device and is retried
 * automatically when the connection returns, or manually.
 *
 * Kept deliberately simple: no background sync, no conflict resolution.
 * Retries are idempotent because the queue id is used as the photo id.
 */
import { toUserMessage } from "@/lib/errors";
import { prepareImage, type PreparedImage } from "@/lib/camera/image-utils";
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
  status: UploadItemStatus;
  /** Local preview (object URL of the thumbnail). */
  thumbnailUrl: string | null;
  error: string | null;
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

export class UploadQueue {
  private readonly db = new IdbDatabase("autoexperten-photo-local", 1, [
    { name: STORE, keyPath: "id", indexes: [{ name: "vehicleId", keyPath: "vehicleId" }] },
  ]);
  private readonly records = new Map<string, PendingUploadRecord>();
  private readonly prepared = new Map<string, Promise<PreparedImage>>();
  private items: UploadItem[] = [];
  private snapshot: UploadQueueSnapshot = EMPTY_SNAPSHOT;
  private readonly listeners = new Set<() => void>();
  private readonly uploadedListeners = new Set<(item: UploadItem) => void>();
  private running = false;
  private restorePromise: Promise<void> | null = null;

  constructor(
    private readonly processor: UploadProcessor,
    private readonly thumbnailMaxEdge: number,
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
        this.records.set(record.id, record);
        this.items.push(this.toItem(record, "queued"));
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
      id: crypto.randomUUID(),
      attempts: 0,
      lastError: null,
      createdAt: new Date().toISOString(),
    };
    this.records.set(record.id, record);
    try {
      await this.db.put(STORE, record);
    } catch {
      // Storage full/blocked: keep it in memory for this session.
    }
    const item = this.toItem(record, "queued");
    this.items = [...this.items, item];
    this.emit();
    void this.ensurePrepared(record);
    void this.run();
    return item;
  }

  retry(id: string): void {
    this.updateItem(id, { status: "queued", error: null });
    void this.run();
  }

  retryAll(): void {
    let changed = false;
    this.items = this.items.map((item) => {
      if (item.status !== "failed") return item;
      changed = true;
      return { ...item, status: "queued", error: null };
    });
    if (changed) {
      this.emit();
      void this.run();
    }
  }

  /** Removes a failed upload permanently (user decision). */
  async discard(id: string): Promise<void> {
    await this.remove(id);
  }

  /* ---------- internals ---------- */

  private toItem(record: PendingUploadRecord, status: UploadItemStatus): UploadItem {
    return {
      id: record.id,
      vehicleId: record.vehicleId,
      shotKey: record.shotKey,
      shotOrder: record.shotOrder,
      title: record.title,
      status,
      thumbnailUrl: null,
      error: record.lastError,
    };
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

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      for (;;) {
        const next = this.items.find((item) => item.status === "queued");
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
          if (done) for (const listener of this.uploadedListeners) listener(done);
        } catch (error) {
          const message = toUserMessage(
            error,
            "Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
          );
          const updated = { ...record, attempts: record.attempts + 1, lastError: message };
          this.records.set(record.id, updated);
          try {
            await this.db.put(STORE, updated);
          } catch {
            // keep in memory
          }
          this.updateItem(record.id, { status: "failed", error: message });
        }
      }
    } finally {
      this.running = false;
    }
  }

  private async remove(id: string): Promise<void> {
    const item = this.items.find((entry) => entry.id === id);
    if (item?.thumbnailUrl) URL.revokeObjectURL(item.thumbnailUrl);
    this.records.delete(id);
    this.prepared.delete(id);
    this.items = this.items.filter((entry) => entry.id !== id);
    this.emit();
    try {
      await this.db.delete(STORE, id);
    } catch {
      // ignore
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
