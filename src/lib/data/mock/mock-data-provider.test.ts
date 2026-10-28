/**
 * Runs against the in-memory fallback of IdbDatabase (jsdom has no IndexedDB).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SavePhotoInput } from "@/lib/data/types";
import type { ProcessingWarning, Vehicle } from "@/lib/domain/types";
import { VehicleChangedError } from "@/lib/errors";
import { createId } from "@/lib/ids";
import { createDemoDatabase, MockDataProvider } from "./mock-data-provider";
import { CURRENT_DEMO_DATA_VERSION, DEMO_STORES, getDemoDataVersion, migrateDemoData } from "./migrations";
import type { StoredPhoto } from "./seed";

const VEHICLE: Vehicle = {
  id: "veh-old",
  userId: "demo-user",
  manufacturer: "Test",
  model: "Test",
  color: null,
  licensePlate: null,
  vin: null,
  mileage: null,
  firstRegistration: null,
  internalReference: null,
  notes: null,
  status: "processed",
  createdAt: "2026-10-01T10:00:00.000Z",
  updatedAt: "2026-10-01T10:00:00.000Z",
};

const FAKE_PROCESSED_PATH = "vehicle-processed/veh-old/autoexperten_standard/front_left_45/photo-old.jpg";

function photo(overrides: Partial<StoredPhoto> = {}): StoredPhoto {
  return {
    id: "photo-old",
    vehicleId: "veh-old",
    shotKey: "front_left_45",
    shotOrder: 1,
    title: "Vorne links (45°)",
    originalStoragePath: "vehicle-originals/veh-old/front_left_45/photo-old.jpg",
    processedStoragePath: FAKE_PROCESSED_PATH,
    processedPreset: "autoexperten_standard",
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 1600,
    height: 1200,
    takenAt: "2026-10-01T10:00:00.000Z",
    createdAt: "2026-10-01T10:00:00.000Z",
    updatedAt: "2026-10-01T10:00:00.000Z",
    archivedAt: null,
    ...overrides,
  };
}

/** A database as left behind by the old app version (simulated processing, no data version). */
async function legacyDatabase() {
  const db = createDemoDatabase(`test-${createId()}`);
  await db.put(DEMO_STORES.meta, { key: "seeded", at: "2026-10-01T10:00:00.000Z" });
  await db.put(DEMO_STORES.vehicles, VEHICLE);
  await db.put(DEMO_STORES.vehicles, { ...VEHICLE, id: "veh-capturing", status: "capturing" });
  await db.put(DEMO_STORES.photos, photo());
  await db.put(DEMO_STORES.photos, photo({ id: "photo-archived", archivedAt: "2026-10-01T11:00:00.000Z" }));
  await db.put(DEMO_STORES.photos, photo({ id: "photo-plain", shotKey: "front", processedStoragePath: null, processedPreset: null }));
  await db.put(DEMO_STORES.files, { path: FAKE_PROCESSED_PATH, blob: new Blob(["original + bar"]) });
  await db.put(DEMO_STORES.files, {
    path: "vehicle-originals/veh-old/front_left_45/photo-old.jpg",
    blob: new Blob(["original"]),
  });
  return db;
}

describe("demo data migration v1 (remove simulated processed versions)", () => {
  it("clears fake processed versions before any data is read", async () => {
    const db = await legacyDatabase();
    const provider = new MockDataProvider(db);

    const vehicles = await provider.listVehicles();
    const old = vehicles.find((item) => item.vehicle.id === "veh-old");
    expect(old?.vehicle.status).toBe("complete");
    expect(old?.photos.every((item) => item.processedStoragePath === null && item.processedPreset === null)).toBe(true);
    expect(vehicles.find((item) => item.vehicle.id === "veh-capturing")?.vehicle.status).toBe("capturing");

    const archived = await db.get<StoredPhoto>(DEMO_STORES.photos, "photo-archived");
    expect(archived?.processedStoragePath).toBeNull();
    expect(await db.get(DEMO_STORES.files, FAKE_PROCESSED_PATH)).toBeUndefined();
    // Originals are never touched.
    expect(await db.get(DEMO_STORES.files, "vehicle-originals/veh-old/front_left_45/photo-old.jpg")).toBeDefined();
    expect(await getDemoDataVersion(db)).toBe(CURRENT_DEMO_DATA_VERSION);
  });

  it("runs only once – real results saved afterwards are kept", async () => {
    const db = await legacyDatabase();
    const first = new MockDataProvider(db);
    const stored = (await first.listPhotos("veh-old")).find((item) => item.id === "photo-old");
    if (!stored) throw new Error("photo missing");
    expect(stored.processedStoragePath).toBeNull();
    await first.saveProcessedPhoto({
      photo: stored,
      preset: "autoexperten_standard",
      file: new Blob(["real showroom result"], { type: "image/jpeg" }),
      warnings: [],
    });

    // A later app start (new provider on the same database) must not clear it again.
    const second = new MockDataProvider(db);
    const photos = await second.listPhotos("veh-old");
    // Same deterministic path as the removed fake – now holding the real result.
    expect(photos.find((item) => item.id === stored.id)?.processedStoragePath).toBe(FAKE_PROCESSED_PATH);
    expect(await db.get<{ blob: Blob }>(DEMO_STORES.files, FAKE_PROCESSED_PATH)).toBeDefined();
    expect(await migrateDemoData(db)).toEqual([]);
  });

  it("is idempotent when run again on already clean data", async () => {
    const db = await legacyDatabase();
    expect(await migrateDemoData(db)).toEqual([1]);
    await db.put(DEMO_STORES.meta, { key: "dataVersion", version: 0, migratedAt: "" });
    expect(await migrateDemoData(db)).toEqual([1]);
    const vehicle = await db.get<Vehicle>(DEMO_STORES.vehicles, "veh-old");
    expect(vehicle?.status).toBe("complete");
  });

  it("marks a fresh database as current without touching the seed", async () => {
    const db = createDemoDatabase(`test-${createId()}`);
    const provider = new MockDataProvider(db);
    const vehicles = await provider.listVehicles();
    expect(vehicles.length).toBeGreaterThan(0);
    expect(await getDemoDataVersion(db)).toBe(CURRENT_DEMO_DATA_VERSION);
  });
});

const NEW_VEHICLE = {
  manufacturer: "VW",
  model: "Golf",
  color: null,
  licensePlate: null,
  vin: null,
  mileage: null,
  firstRegistration: null,
  internalReference: null,
  notes: null,
};

describe("MockDataProvider.savePhoto – captures that arrive out of order", () => {
  async function setup() {
    const db = createDemoDatabase(`test-${createId()}`);
    const provider = new MockDataProvider(db);
    const vehicle = await provider.createVehicle(NEW_VEHICLE);
    const capture = (photoId: string, takenAt: string): SavePhotoInput => ({
      photoId,
      vehicleId: vehicle.id,
      shotKey: "rear",
      shotOrder: 7,
      title: "Hinten",
      file: new Blob([photoId], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt,
    });
    return { db, provider, vehicle, capture };
  }

  it("stores a capture older than the active photo as archived and keeps the newer photo", async () => {
    const { db, provider, vehicle, capture } = await setup();
    await provider.savePhoto(capture("retake", "2026-10-28T10:00:30.000Z"));
    // The first capture's upload failed and is retried after the retake was saved.
    await provider.savePhoto(capture("first", "2026-10-28T10:00:00.000Z"));

    expect((await provider.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual(["retake"]);
    const late = await db.get<StoredPhoto>(DEMO_STORES.photos, "first");
    expect(late?.archivedAt).not.toBeNull();
    // The late original is still stored (originals are never lost or overwritten).
    expect(await db.get(DEMO_STORES.files, late?.originalStoragePath ?? "")).toBeDefined();
    expect((await db.get<StoredPhoto>(DEMO_STORES.photos, "retake"))?.archivedAt).toBeNull();
  });

  it("archives the active photo for a newer or equally old capture (a retake)", async () => {
    const { db, provider, vehicle, capture } = await setup();
    await provider.savePhoto(capture("first", "2026-10-28T10:00:00.000Z"));
    await provider.savePhoto(capture("same-time", "2026-10-28T10:00:00.000Z"));
    expect((await provider.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual(["same-time"]);
    await provider.savePhoto(capture("retake", "2026-10-28T10:05:00.000Z"));
    expect((await provider.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual(["retake"]);
    expect((await db.get<StoredPhoto>(DEMO_STORES.photos, "first"))?.archivedAt).not.toBeNull();
    expect((await db.get<StoredPhoto>(DEMO_STORES.photos, "same-time"))?.archivedAt).not.toBeNull();
  });

  it("decides like the database: a photo dated ahead counts as taken at most 5 minutes after it was saved", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-28T10:00:00.000Z"), toFake: ["Date"] });
    try {
      const { provider, vehicle, capture } = await setup();
      // Saved at 10:00 from a camera whose clock runs a month ahead.
      await provider.savePhoto(capture("ahead", "2026-11-28T10:00:00.000Z"));
      // A capture with the real time 3 minutes later still counts as the older one (add_vehicle_photo) …
      vi.setSystemTime(new Date("2026-10-28T10:03:00.000Z"));
      await provider.savePhoto(capture("early", "2026-10-28T10:03:00.000Z"));
      expect((await provider.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual(["ahead"]);
      // … from 10:05 on the retake replaces it, with its real capture time.
      vi.setSystemTime(new Date("2026-10-28T10:06:00.000Z"));
      await provider.savePhoto(capture("retake", "2026-10-28T10:06:00.000Z"));
      const [current] = await provider.listPhotos(vehicle.id);
      expect([current?.id, current?.takenAt]).toEqual(["retake", "2026-10-28T10:06:00.000Z"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stays idempotent when a late capture is retried", async () => {
    const { provider, vehicle, capture } = await setup();
    await provider.savePhoto(capture("retake", "2026-10-28T10:00:30.000Z"));
    await provider.savePhoto(capture("first", "2026-10-28T10:00:00.000Z"));
    await provider.savePhoto(capture("first", "2026-10-28T10:00:00.000Z"));
    expect((await provider.listPhotos(vehicle.id)).map((photo) => photo.id)).toEqual(["retake"]);
  });
});

const REMOVED: ProcessingWarning = {
  code: "background_object_removed",
  message:
    "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
};

describe("MockDataProvider – the processor's hints on a processed version", () => {
  async function savedPhoto(provider: MockDataProvider, vehicleId: string, photoId: string, takenAt: string) {
    return provider.savePhoto({
      photoId,
      vehicleId,
      shotKey: "rear",
      shotOrder: 7,
      title: "Hinten",
      file: new Blob([photoId], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt,
    });
  }
  const result = new Blob(["showroom"], { type: "image/jpeg" });

  it("keeps them with the result, replaces them with a new processing and starts a new photo without", async () => {
    const provider = new MockDataProvider(createDemoDatabase(`test-${createId()}`));
    const vehicle = await provider.createVehicle(NEW_VEHICLE);
    const photo = await savedPhoto(provider, vehicle.id, "first", "2026-10-28T10:00:00.000Z");
    expect(photo.processingWarnings).toEqual([]);
    const hints = () => provider.listPhotos(vehicle.id).then((photos) => photos.map((item) => [item.id, item.processingWarnings]));

    await provider.saveProcessedPhoto({ photo, preset: "autoexperten_standard", file: result, warnings: [REMOVED] });
    expect(await hints()).toEqual([["first", [REMOVED]]]);
    await provider.saveProcessedPhoto({ photo, preset: "autoexperten_standard", file: result, warnings: [] });
    expect(await hints()).toEqual([["first", []]]);

    await provider.saveProcessedPhoto({ photo, preset: "autoexperten_standard", file: result, warnings: [REMOVED] });
    await savedPhoto(provider, vehicle.id, "retake", "2026-10-28T10:05:00.000Z");
    expect(await hints()).toEqual([["retake", []]]);
  });

  it("reads records of older app versions (no hints stored) as without hints", async () => {
    const db = createDemoDatabase(`test-${createId()}`);
    const provider = new MockDataProvider(db);
    const vehicle = await provider.createVehicle(NEW_VEHICLE);
    const photo = await savedPhoto(provider, vehicle.id, "legacy", "2026-10-28T10:00:00.000Z");
    await provider.saveProcessedPhoto({ photo, preset: "autoexperten_standard", file: result, warnings: [] });
    const record = await db.get<StoredPhoto>(DEMO_STORES.photos, "legacy");
    if (!record) throw new Error("record missing");
    Reflect.deleteProperty(record, "processingWarnings");
    await db.put(DEMO_STORES.photos, record);

    const [listed] = await provider.listPhotos(vehicle.id);
    expect(listed?.processedStoragePath).not.toBeNull();
    expect(listed?.processingWarnings).toEqual([]);
  });
});

describe("MockDataProvider.updateVehicle – on the data it is based on", () => {
  it("never saves over a change made since the data was loaded", async () => {
    const provider = new MockDataProvider(createDemoDatabase(`test-${createId()}`));
    const loaded = await provider.createVehicle(NEW_VEHICLE);
    const elsewhere = await provider.updateVehicle(loaded.id, { ...NEW_VEHICLE, notes: "Delle Tür hinten" });

    await expect(
      provider.updateVehicle(loaded.id, { ...NEW_VEHICLE, color: "Rot" }, { expectedUpdatedAt: loaded.updatedAt }),
    ).rejects.toBeInstanceOf(VehicleChangedError);
    expect((await provider.getVehicle(loaded.id))?.notes).toBe("Delle Tür hinten");

    const saved = await provider.updateVehicle(
      loaded.id,
      { ...NEW_VEHICLE, notes: "Delle Tür hinten", color: "Rot" },
      { expectedUpdatedAt: elsewhere.updatedAt },
    );
    expect([saved.color, saved.notes]).toEqual(["Rot", "Delle Tür hinten"]);
  });

  it("gives every change a later updatedAt – also two in the same millisecond", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-28T10:00:00.000Z"), toFake: ["Date"] });
    try {
      const provider = new MockDataProvider(createDemoDatabase(`test-${createId()}`));
      const created = await provider.createVehicle(NEW_VEHICLE);
      const first = await provider.updateVehicle(created.id, { ...NEW_VEHICLE, notes: "eins" });
      await provider.setVehicleStatus(created.id, "capturing");
      const second = await provider.getVehicle(created.id);
      expect(new Set([created.updatedAt, first.updatedAt, second?.updatedAt]).size).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("MockDataProvider over plain HTTP (no crypto.randomUUID)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates demo vehicles", async () => {
    const provider = new MockDataProvider(createDemoDatabase(`test-${createId()}`));
    const { crypto } = globalThis;
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const vehicle = await provider.createVehicle(NEW_VEHICLE);
    expect(vehicle.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect((await provider.getVehicle(vehicle.id))?.model).toBe("Golf");
  });
});
