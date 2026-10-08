/**
 * Runs against the in-memory fallback of IdbDatabase (jsdom has no IndexedDB).
 */
import { describe, expect, it } from "vitest";
import type { Vehicle } from "@/lib/domain/types";
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
  const db = createDemoDatabase(`test-${crypto.randomUUID()}`);
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
    const db = createDemoDatabase(`test-${crypto.randomUUID()}`);
    const provider = new MockDataProvider(db);
    const vehicles = await provider.listVehicles();
    expect(vehicles.length).toBeGreaterThan(0);
    expect(await getDemoDataVersion(db)).toBe(CURRENT_DEMO_DATA_VERSION);
  });
});
