/**
 * Loading the export files through the data backends' test doubles: the demo
 * provider (in-memory IndexedDB fallback) and the Supabase provider over a
 * fake Storage server.
 */
import { createClient } from "@supabase/supabase-js";
import { describe, expect, it, vi } from "vitest";
import { createDemoDatabase, MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import { SupabaseDataProvider } from "@/lib/data/supabase/supabase-data-provider";
import type { DataProvider } from "@/lib/data/types";
import type { VehiclePhoto } from "@/lib/domain/types";
import { createId } from "@/lib/ids";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T, getOrderedShots } from "@/lib/shots/shot-template";
import { buildExportZip, ExportFileLoader, exportFileKey, loadExportFile } from "./export-files";
import { buildExportPlan, selectExportItems, type ExportItem } from "./listing-export";
import { crc32 } from "./zip";

const TAKEN = "2026-11-05T09:00:00.000Z";

/** A demo vehicle with all 15 shots; the first two processed. */
async function demoVehicle() {
  const data = new MockDataProvider(createDemoDatabase(`export-${createId()}`));
  const vehicle = await data.createVehicle({
    manufacturer: "Mercedes-Benz",
    model: "E 220 d",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: "FZ-5001",
    notes: null,
  });
  for (const shot of getOrderedShots(T)) {
    const photo = await data.savePhoto({
      photoId: createId(),
      vehicleId: vehicle.id,
      shotKey: shot.key,
      shotOrder: shot.order,
      title: shot.title,
      file: new Blob([`original ${shot.key}`], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: TAKEN,
    });
    if (shot.order <= 2) {
      await data.saveProcessedPhoto({
        photo,
        preset: "autoexperten_standard",
        file: new Blob([`processed ${shot.key}`], { type: "image/jpeg" }),
        warnings: [],
      });
    }
  }
  const photos = await data.resolvePhotoUrls(await data.listPhotos(vehicle.id));
  const plan = buildExportPlan({ template: T, vehicle, slots: buildPhotoSlots(T, photos) });
  return { data, vehicle, plan };
}

async function loadAll(data: DataProvider, items: readonly ExportItem[]) {
  const loader = new ExportFileLoader(() => data);
  loader.want(items);
  await vi.waitFor(() => expect(loader.getSnapshot().loading).toBe(0));
  return loader;
}

describe("export files in demo mode (IndexedDB)", () => {
  it("loads every file of the listing set under its export name – processed or original", async () => {
    const { data, plan } = await demoVehicle();
    const items = selectExportItems(plan);
    const loader = await loadAll(data, items);
    const { files, errors } = loader.getSnapshot();
    expect(errors.size).toBe(0);
    const ordered = items.map((item) => files.get(exportFileKey(item)));
    expect(ordered.map((file) => file?.name)).toEqual(
      getOrderedShots(T).map((shot) => `AE_FZ-5001_${String(shot.order).padStart(2, "0")}_${shot.key}.jpg`),
    );
    expect(await ordered[0]?.text()).toBe("processed front_left_45");
    expect(await ordered[1]?.text()).toBe("processed front");
    expect(await ordered[2]?.text()).toBe("original front_right_45");
    expect(ordered.every((file) => file?.type === "image/jpeg")).toBe(true);
    expect(ordered[0]?.lastModified).toBe(Date.parse(TAKEN));
  });

  it("builds a ZIP of the files in listing order with their CRC-32", async () => {
    const { data, plan } = await demoVehicle();
    const items = selectExportItems(plan).slice(0, 3);
    const loader = await loadAll(data, items);
    const files = items.map((item) => loader.getSnapshot().files.get(exportFileKey(item))).filter((f) => f !== undefined);
    const zip = new Uint8Array(await (await buildExportZip(files, loader.crcOf)).arrayBuffer());
    const view = new DataView(zip.buffer);
    // First local header: name and CRC of the first file.
    expect(view.getUint32(0, true)).toBe(0x04034b50);
    const nameLength = view.getUint16(26, true);
    expect(new TextDecoder().decode(zip.subarray(30, 30 + nameLength))).toBe("AE_FZ-5001_01_front_left_45.jpg");
    expect(view.getUint32(14, true)).toBe(crc32(new TextEncoder().encode("processed front_left_45")));
    // End record: three entries.
    expect(view.getUint16(zip.length - 22 + 10, true)).toBe(3);
  });

  it("reports a file that cannot be loaded and loads it again only on request", async () => {
    const { data, plan } = await demoVehicle();
    const [first] = selectExportItems(plan);
    if (!first) throw new Error("no item");
    const getPhotoFile = vi.spyOn(data, "getPhotoFile").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const loader = await loadAll(data, [first]);
    expect([...loader.getSnapshot().errors.values()]).toEqual(["Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut."]);
    loader.want([first]);
    expect(getPhotoFile).toHaveBeenCalledTimes(1);
    loader.want([first], { retry: true });
    await vi.waitFor(() => expect(loader.getSnapshot().files.size).toBe(1));
    expect(loader.getSnapshot().errors.size).toBe(0);
  });

  it("loads at most three files at a time and drops answers after clear()", async () => {
    const { data, plan } = await demoVehicle();
    let running = 0;
    let most = 0;
    const real = data.getPhotoFile.bind(data);
    vi.spyOn(data, "getPhotoFile").mockImplementation(async (photo, version) => {
      running += 1;
      most = Math.max(most, running);
      await new Promise((resolve) => setTimeout(resolve, 5));
      running -= 1;
      return real(photo, version);
    });
    const loader = new ExportFileLoader(() => data);
    loader.want(selectExportItems(plan));
    expect(loader.getSnapshot().loading).toBe(15);
    loader.clear();
    expect(loader.getSnapshot()).toMatchObject({ loading: 0 });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(loader.getSnapshot().files.size).toBe(0);
    expect(most).toBeLessThanOrEqual(3);
  });
});

/**
 * A Response whose blob() is a Blob of the test's DOM (jsdom): Node's own
 * Response gives a Node Blob, which jsdom's File cannot wrap – in a browser
 * both are the same Blob.
 */
class DomBlobResponse extends Response {
  override async blob(): Promise<Blob> {
    const type = this.headers.get("content-type") ?? "";
    return new Blob([await this.text()], { type });
  }
}

describe("export files in Supabase mode (Storage download)", () => {
  it("downloads the processed result and the original with the session's storage access", async () => {
    const VEHICLE_ID = "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301";
    const base = {
      vehicleId: VEHICLE_ID,
      title: "",
      processedPreset: null,
      processingWarnings: [],
      thumbnailStoragePath: null,
      width: 4000,
      height: 3000,
      takenAt: TAKEN,
      createdAt: TAKEN,
      updatedAt: TAKEN,
    };
    const photos: VehiclePhoto[] = [
      {
        ...base,
        id: "p1",
        shotKey: "front_left_45",
        shotOrder: 1,
        originalStoragePath: `${VEHICLE_ID}/front_left_45/p1.jpg`,
        processedStoragePath: `${VEHICLE_ID}/autoexperten_standard/front_left_45/p1.jpg`,
        processedPreset: "autoexperten_standard",
      },
      {
        ...base,
        id: "p2",
        shotKey: "front",
        shotOrder: 2,
        originalStoragePath: `${VEHICLE_ID}/front/p2.heic`,
        processedStoragePath: null,
      },
    ];
    const objects: Record<string, [string, string]> = {
      [`vehicle-processed/${VEHICLE_ID}/autoexperten_standard/front_left_45/p1.jpg`]: ["processed fl", "image/jpeg"],
      [`vehicle-originals/${VEHICLE_ID}/front/p2.heic`]: ["original front", "image/heic"],
    };
    const requested: string[] = [];
    const client = createClient("https://project.supabase.co", "anon-key", {
      global: {
        fetch: (async (input: RequestInfo | URL) => {
          const url = new URL(input instanceof Request ? input.url : String(input));
          const key = decodeURIComponent(url.pathname.replace(/^\/storage\/v1\/object\//, ""));
          requested.push(key);
          const object = objects[key];
          return object
            ? new DomBlobResponse(object[0], { headers: { "content-type": object[1] } })
            : new Response(JSON.stringify({ statusCode: "404", message: "Object not found" }), { status: 400 });
        }) as typeof fetch,
      },
      auth: { persistSession: false, autoRefreshToken: false },
    });
    const data = new SupabaseDataProvider(client);
    const vehicle = { id: VEHICLE_ID, internalReference: "FZ-2041" };
    const withUrls = photos.map((photo) => ({ ...photo, urls: { original: "", thumbnail: "", processed: null } }));
    const plan = buildExportPlan({ template: T, vehicle, slots: buildPhotoSlots(T, withUrls) });
    const [processed, original] = selectExportItems(plan);
    if (!processed || !original) throw new Error("missing items");

    const first = await loadExportFile(data, processed);
    const second = await loadExportFile(data, original);

    expect([first.name, first.type, await first.text()]).toEqual(["AE_FZ-2041_01_front_left_45.jpg", "image/jpeg", "processed fl"]);
    expect([second.name, second.type, await second.text()]).toEqual(["AE_FZ-2041_02_front.heic", "image/heic", "original front"]);
    expect(requested).toEqual(Object.keys(objects));
  });
});
