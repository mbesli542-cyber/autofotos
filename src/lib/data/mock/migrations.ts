/**
 * Versioned data migrations of the demo database (IndexedDB, MockDataProvider).
 *
 * The applied version is kept in the `meta` store (`{ key: "dataVersion" }`).
 * Migrations run once, in order, before any demo data is read; each one is
 * also safe to run again (idempotent).
 *
 * v1 – Remove fake processed versions. Before the real processor was
 *      connected, demo mode "processed" photos with a simulated preview (the
 *      ORIGINAL photo plus a branding bar). Those are not AutoExperten
 *      results: their files and links are deleted and vehicles in status
 *      "processed" go back to "complete".
 */
import type { Vehicle } from "@/lib/domain/types";
import type { IdbDatabase } from "@/lib/offline/idb";
import type { StoredPhoto } from "./seed";

export const DEMO_STORES = {
  vehicles: "vehicles",
  photos: "photos",
  files: "files",
  meta: "meta",
} as const;

export const DATA_VERSION_KEY = "dataVersion";

interface DataVersionRecord {
  key: typeof DATA_VERSION_KEY;
  version: number;
  migratedAt: string;
}

type Migration = (db: IdbDatabase) => Promise<void>;

async function removeSimulatedProcessedVersions(db: IdbDatabase): Promise<void> {
  const now = new Date().toISOString();
  const photos = await db.getAll<StoredPhoto>(DEMO_STORES.photos);
  for (const photo of photos) {
    if (photo.processedStoragePath === null && photo.processedPreset === null) continue;
    if (photo.processedStoragePath) await db.delete(DEMO_STORES.files, photo.processedStoragePath);
    await db.put<StoredPhoto>(DEMO_STORES.photos, {
      ...photo,
      processedStoragePath: null,
      processedPreset: null,
      updatedAt: now,
    });
  }
  const vehicles = await db.getAll<Vehicle>(DEMO_STORES.vehicles);
  for (const vehicle of vehicles) {
    if (vehicle.status !== "processed") continue;
    await db.put<Vehicle>(DEMO_STORES.vehicles, { ...vehicle, status: "complete", updatedAt: now });
  }
}

/** Index i holds the migration to version i + 1. */
export const DEMO_MIGRATIONS: readonly Migration[] = [removeSimulatedProcessedVersions];

export const CURRENT_DEMO_DATA_VERSION = DEMO_MIGRATIONS.length;

export async function getDemoDataVersion(db: IdbDatabase): Promise<number> {
  const record = await db.get<DataVersionRecord>(DEMO_STORES.meta, DATA_VERSION_KEY);
  return typeof record?.version === "number" ? record.version : 0;
}

export async function setDemoDataVersion(db: IdbDatabase, version: number): Promise<void> {
  await db.put<DataVersionRecord>(DEMO_STORES.meta, {
    key: DATA_VERSION_KEY,
    version,
    migratedAt: new Date().toISOString(),
  });
}

/** Applies all pending migrations. Returns the versions that were applied. */
export async function migrateDemoData(db: IdbDatabase): Promise<number[]> {
  const applied: number[] = [];
  let version = await getDemoDataVersion(db);
  while (version < CURRENT_DEMO_DATA_VERSION) {
    const migration = DEMO_MIGRATIONS[version];
    if (!migration) break;
    await migration(db);
    version += 1;
    await setDemoDataVersion(db, version);
    applied.push(version);
  }
  return applied;
}
