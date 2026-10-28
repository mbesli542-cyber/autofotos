/**
 * Real UploadQueue wired like app-services.ts (savePhotoAndSyncStatus on the
 * demo MockDataProvider). jsdom has no IndexedDB: both use the in-memory
 * fallback of IdbDatabase; sharing one queue database between two queues
 * simulates the next app start.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/camera/image-utils", () => ({
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));

import { createDemoDatabase, MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import { AppError, USER_ERROR_MESSAGES } from "@/lib/errors";
import { createId } from "@/lib/ids";
import { DEFAULT_SHOT_TEMPLATE } from "@/lib/shots/shot-template";
import { savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import type { IdbDatabase } from "./idb";
import {
  createUploadQueueDatabase,
  UploadQueue,
  type PendingUploadRecord,
  type UploadProcessor,
} from "./upload-queue";

const flush = () => new Promise((resolve) => setTimeout(resolve, 20));

const T0 = "2026-10-28T10:00:00.000Z";
const T1 = "2026-10-28T10:00:30.000Z";

let urls: { created: string[]; revoked: string[] };

beforeEach(() => {
  urls = { created: [], revoked: [] };
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:preview-${urls.created.length + 1}`;
    urls.created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => {
    urls.revoked.push(url);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function setup() {
  const data = new MockDataProvider(createDemoDatabase(`queue-test-${createId()}`));
  const vehicle = await data.createVehicle({
    manufacturer: "VW",
    model: "Golf",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  /** Errors thrown by the next upload attempts (then uploads succeed). */
  const failures: unknown[] = [];
  const save = vi.fn<UploadProcessor>(async (record, prepared) => {
    if (failures.length > 0) throw failures.shift();
    await savePhotoAndSyncStatus(data, DEFAULT_SHOT_TEMPLATE, {
      photoId: record.id,
      vehicleId: record.vehicleId,
      shotKey: record.shotKey,
      shotOrder: record.shotOrder,
      title: record.title,
      file: record.file,
      thumbnail: prepared.thumbnail,
      width: prepared.width,
      height: prepared.height,
      takenAt: record.takenAt,
    });
  });
  const capture = (takenAt: string, shotKey = "rear") => ({
    vehicleId: vehicle.id,
    shotKey,
    shotOrder: 7,
    title: "Hinten",
    file: new Blob([takenAt], { type: "image/jpeg" }),
    takenAt,
  });
  return { data, vehicle, failures, save, capture };
}

async function persisted(db: IdbDatabase) {
  return (await db.getAll<PendingUploadRecord>("pendingUploads")).map((record) => record.id);
}

describe("UploadQueue – captures uploaded out of order (retake while an older upload failed)", () => {
  it("drops the older failed capture once the newer retake is saved", async () => {
    const { data, vehicle, failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);

    failures.push(new TypeError("Failed to fetch")); // weak Wi-Fi
    const older = await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items.map((item) => [item.id, item.status])).toEqual([[older.id, "failed"]]);
    const olderPreview = queue.getSnapshot().items[0]?.thumbnailUrl;

    const retake = await queue.enqueue(capture(T1));
    await flush();

    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(olderPreview).toBeTruthy();
    expect(urls.revoked).toContain(olderPreview);

    // "Erneut versuchen" / the next "online" event: nothing left that could replace the retake.
    queue.retryAll();
    await flush();
    expect((await data.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual([retake.id]);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("does not re-queue a stored older capture at the next app start once the newer one was saved", async () => {
    const { data, vehicle, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    // Left over from an earlier session (failed upload of the first capture).
    const stale: PendingUploadRecord = {
      ...capture(T0),
      id: createId(),
      attempts: 1,
      lastError: USER_ERROR_MESSAGES.network,
      createdAt: T0,
    };
    await db.put("pendingUploads", stale);

    const queue = new UploadQueue(save, 480, db);
    const retake = await queue.enqueue(capture(T1));
    await flush();
    await queue.restore(); // AuthGate, after the retake was saved
    await flush();

    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect((await data.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual([retake.id]);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("keeps an older capture of ANOTHER shot or vehicle", async () => {
    const { failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new TypeError("Failed to fetch"));
    const front = await queue.enqueue(capture(T0, "front"));
    await flush();
    await queue.enqueue(capture(T1, "rear"));
    await flush();
    expect(queue.getSnapshot().items.map((item) => [item.id, item.status])).toEqual([[front.id, "failed"]]);
  });
});

describe("UploadQueue – files the server refuses for good", () => {
  it("marks unsupported types as final: not retried by retryAll, restore or 'online'", async () => {
    const { failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new AppError("unsupported_file"));

    const item = await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items[0]).toMatchObject({
      id: item.id,
      status: "failed",
      retryable: false,
      error: "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).",
    });

    queue.retryAll();
    await flush();
    expect(save).toHaveBeenCalledTimes(1);

    // Next app start: the stored record stays failed and is not uploaded again.
    const nextStart = new UploadQueue(save, 480, db);
    await nextStart.restore();
    window.dispatchEvent(new Event("online"));
    await flush();
    expect(nextStart.getSnapshot().items).toMatchObject([{ id: item.id, status: "failed", retryable: false }]);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("marks files that are too large as final", async () => {
    const { failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new AppError("file_too_large"));
    await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items[0]).toMatchObject({
      status: "failed",
      retryable: false,
      error: "Das Foto ist zu groß (max. 50 MB).",
    });
  });

  it("keeps network, session and server errors retryable", async () => {
    const { failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new TypeError("Failed to fetch"), new AppError("unauthorized"), new AppError("storage"));
    await queue.enqueue(capture(T0));
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await flush();
      expect(queue.getSnapshot().items[0]).toMatchObject({ status: "failed", retryable: true });
      queue.retryAll();
    }
    await flush();
    expect(queue.getSnapshot().items).toEqual([]);
    expect(save).toHaveBeenCalledTimes(4);
  });

  it("still retries a refused file when the employee explicitly asks for it", async () => {
    const { failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new AppError("unsupported_file"));
    const item = await queue.enqueue(capture(T0));
    await flush();
    queue.retry(item.id);
    await flush();
    expect(queue.getSnapshot().items).toEqual([]);
    expect(save).toHaveBeenCalledTimes(2);
  });
});

describe("UploadQueue.discard", () => {
  it("removes a failed capture with its persisted record and preview", async () => {
    const { failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new AppError("unsupported_file"));
    const item = await queue.enqueue(capture(T0));
    await flush();
    const preview = queue.getSnapshot().items[0]?.thumbnailUrl;
    expect(preview).toBeTruthy();

    await queue.discard(item.id);
    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(urls.revoked).toContain(preview);

    // Not restored at the next app start either.
    const nextStart = new UploadQueue(save, 480, db);
    await nextStart.restore();
    expect(nextStart.getSnapshot().items).toEqual([]);
  });

  it("keeps the capture and passes the error on when its stored record cannot be deleted", async () => {
    const { failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new AppError("unsupported_file"));
    const item = await queue.enqueue(capture(T0));
    await flush();
    const remove = vi.spyOn(db, "delete").mockRejectedValueOnce(new DOMException("Datenbankfehler", "UnknownError"));

    await expect(queue.discard(item.id)).rejects.toThrow();
    // Still there – it would otherwise come back (and be uploaded) at the next start.
    expect(queue.getSnapshot().items.map((entry) => entry.id)).toEqual([item.id]);
    expect(await persisted(db)).toEqual([item.id]);

    // The next attempt removes it for good.
    remove.mockRestore();
    await queue.discard(item.id);
    expect(queue.getSnapshot().items).toEqual([]);
    const nextStart = new UploadQueue(save, 480, db);
    await nextStart.restore();
    expect(nextStart.getSnapshot().items).toEqual([]);
  });

  it("neither uploads nor retries a capture while its stored record is being deleted", async () => {
    const { data, vehicle, failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new TypeError("Failed to fetch"));
    const item = await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items[0]?.status).toBe("failed");

    let finishDelete: () => void = () => undefined;
    const realDelete = db.delete.bind(db);
    vi.spyOn(db, "delete").mockImplementationOnce(async (store, key) => {
      await new Promise<void>((resolve) => (finishDelete = resolve));
      return realDelete(store, key);
    });
    const discarding = queue.discard(item.id);
    // Meanwhile the connection is back ("online") or "Erneut versuchen" is tapped.
    queue.retryAll();
    expect(queue.getSnapshot().items[0]?.status).toBe("failed");
    queue.retry(item.id);
    expect(queue.getSnapshot().items[0]?.status).toBe("failed");
    await flush();
    expect(save).toHaveBeenCalledTimes(1);

    finishDelete();
    await discarding;
    await flush();
    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(save).toHaveBeenCalledTimes(1);
    expect(await data.listPhotos(vehicle.id)).toEqual([]);
  });

  it("uploads a waiting capture after all when its stored record could not be deleted", async () => {
    const { capture } = await setup();
    const db = createUploadQueueDatabase();
    let finishFirst: () => void = () => undefined;
    const uploaded: string[] = [];
    const save = vi.fn<UploadProcessor>(async (record) => {
      if (uploaded.length === 0) await new Promise<void>((resolve) => (finishFirst = resolve));
      uploaded.push(record.id);
    });
    const queue = new UploadQueue(save, 480, db);
    const first = await queue.enqueue(capture(T0, "front"));
    const waiting = await queue.enqueue(capture(T1));
    await flush();
    expect(queue.getSnapshot().items.map((entry) => entry.status)).toEqual(["uploading", "queued"]);

    let failDelete: () => void = () => undefined;
    vi.spyOn(db, "delete").mockImplementationOnce(
      () => new Promise<void>((_, reject) => (failDelete = () => reject(new DOMException("Datenbankfehler", "UnknownError")))),
    );
    const discarding = queue.discard(waiting.id);
    finishFirst();
    await flush();
    // Not uploaded while it is being discarded …
    expect(uploaded).toEqual([first.id]);

    failDelete();
    await expect(discarding).rejects.toThrow();
    await flush();
    // … but it stays, so it is saved like any other capture.
    expect(uploaded).toEqual([first.id, waiting.id]);
    expect(queue.getSnapshot().items).toEqual([]);
  });

  it("discards a capture that was only kept in memory (the storage refused it)", async () => {
    const { failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    vi.spyOn(db, "put").mockRejectedValue(new DOMException("Speicher voll", "QuotaExceededError"));
    const queue = new UploadQueue(save, 480, db);
    failures.push(new AppError("unsupported_file"));
    const item = await queue.enqueue(capture(T0));
    await flush();
    // There is no stored record to delete – a broken storage does not keep it.
    vi.spyOn(db, "delete").mockRejectedValue(new DOMException("Datenbankfehler", "UnknownError"));
    await queue.discard(item.id);
    expect(queue.getSnapshot().items).toEqual([]);
  });

  it("does not interrupt an upload in progress", async () => {
    const { capture } = await setup();
    let finish: () => void = () => undefined;
    const save = vi.fn<UploadProcessor>(() => new Promise<void>((resolve) => (finish = resolve)));
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    const item = await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items[0]?.status).toBe("uploading");
    await queue.discard(item.id);
    expect(queue.getSnapshot().items[0]?.status).toBe("uploading");
    finish();
    await flush();
    expect(queue.getSnapshot().items).toEqual([]);
  });
});

describe("UploadQueue.dropRefusedOlderThan (a newer photo was saved elsewhere)", () => {
  const saved = (vehicleId: string, shotKey: string, takenAt: string, createdAt = takenAt) => ({
    vehicleId,
    shotKey,
    takenAt,
    createdAt,
  });

  it("drops a refused capture older than the saved photo of its shot, with its record and preview", async () => {
    const { vehicle, failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new AppError("unsupported_file"));
    const refused = await queue.enqueue(capture(T0));
    await flush();
    const preview = queue.getSnapshot().items[0]?.thumbnailUrl;
    expect(queue.getSnapshot().items).toMatchObject([{ id: refused.id, status: "failed", retryable: false }]);

    // e.g. a retake saved on another device – the slot shows that photo.
    await queue.dropRefusedOlderThan([saved(vehicle.id, "rear", T1)]);
    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(urls.revoked).toContain(preview);
  });

  it("keeps captures that are shown in their slot or can still be saved", async () => {
    const { vehicle, failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new AppError("unsupported_file"), new AppError("file_too_large"), new TypeError("Failed to fetch"));
    const newerThanSaved = await queue.enqueue(capture(T1, "rear"));
    const otherVehicle = await queue.enqueue(capture(T0, "front"));
    const retryable = await queue.enqueue(capture(T0, "left_side"));
    await flush();

    await queue.dropRefusedOlderThan([
      saved(vehicle.id, "rear", T0),
      saved("another-vehicle", "front", T1),
      // Uploaded later, it is stored as an archived copy.
      saved(vehicle.id, "left_side", T1),
    ]);
    expect(queue.getSnapshot().items.map((item) => item.id)).toEqual([
      newerThanSaved.id,
      otherVehicle.id,
      retryable.id,
    ]);
  });

  it("decides like the photo slots: a photo dated ahead counts as taken at most 5 minutes after it was saved", async () => {
    const { vehicle, failures, save, capture } = await setup();
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    failures.push(new AppError("unsupported_file"), new AppError("unsupported_file"));
    const afterCap = await queue.enqueue(capture("2026-10-28T10:06:00.000Z", "rear"));
    await queue.enqueue(capture("2026-10-28T10:04:00.000Z", "front"));
    await flush();

    // Both shots saved on another device at 10:00 – its camera clock a month ahead.
    await queue.dropRefusedOlderThan([
      saved(vehicle.id, "rear", "2026-11-28T10:00:00.000Z", "2026-10-28T10:00:00.000Z"),
      saved(vehicle.id, "front", "2026-11-28T10:00:00.000Z", "2026-10-28T10:00:00.000Z"),
    ]);
    // 10:06 is newer than 10:05 – shown in its slot (and discarded there); 10:04 is hidden behind the photo.
    expect(queue.getSnapshot().items.map((item) => item.id)).toEqual([afterCap.id]);
  });

  it("also drops a refused capture that is only being restored from an earlier session", async () => {
    const { vehicle, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const stored: PendingUploadRecord = {
      ...capture(T0),
      id: createId(),
      attempts: 1,
      lastError: USER_ERROR_MESSAGES.unsupported_file,
      retryable: false,
      createdAt: T0,
    };
    await db.put("pendingUploads", stored);

    const queue = new UploadQueue(save, 480, db);
    const restoring = queue.restore(); // AuthGate – the page loads its data meanwhile
    await queue.dropRefusedOlderThan([saved(vehicle.id, "rear", T1)]);
    await restoring;
    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(save).not.toHaveBeenCalled();
  });
});

describe("UploadQueue.clear (demo reset)", () => {
  it("removes every capture, its preview and the persisted records", async () => {
    const { failures, save, capture } = await setup();
    const db = createUploadQueueDatabase();
    const queue = new UploadQueue(save, 480, db);
    failures.push(new TypeError("Failed to fetch"), new AppError("unsupported_file"));
    await queue.enqueue(capture(T0, "front"));
    await queue.enqueue(capture(T1, "rear"));
    await flush();
    const previews = queue.getSnapshot().items.map((item) => item.thumbnailUrl);
    expect(previews).toHaveLength(2);

    await queue.clear();
    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
    expect(urls.revoked).toEqual(expect.arrayContaining(previews));
    queue.retryAll();
    await flush();
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("waits for an upload in progress and never stores it again when it fails", async () => {
    const { capture } = await setup();
    const db = createUploadQueueDatabase();
    let fail: (error: unknown) => void = () => undefined;
    const save = vi.fn<UploadProcessor>(() => new Promise<void>((_, reject) => (fail = reject)));
    const queue = new UploadQueue(save, 480, db);
    await queue.enqueue(capture(T0));
    await flush();
    expect(queue.getSnapshot().items[0]?.status).toBe("uploading");

    let cleared = false;
    const clearing = queue.clear().then(() => (cleared = true));
    await flush();
    expect(cleared).toBe(false);
    fail(new TypeError("Failed to fetch"));
    await clearing;

    expect(queue.getSnapshot().items).toEqual([]);
    expect(await persisted(db)).toEqual([]);
  });
});

describe("UploadQueue over plain HTTP (no crypto.randomUUID)", () => {
  it("queues a picked photo ('Foto hochladen')", async () => {
    const { capture, save } = await setup();
    const { crypto } = globalThis;
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const queue = new UploadQueue(save, 480, createUploadQueueDatabase());
    const item = await queue.enqueue(capture(T0));
    expect(item.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
