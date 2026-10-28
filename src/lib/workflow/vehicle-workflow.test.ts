/** Status sync of savePhotoAndSyncStatus on the demo provider (in-memory IndexedDB fallback). */
import { describe, expect, it, vi } from "vitest";
import { createDemoDatabase, MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import type { VehicleInput } from "@/lib/domain/types";
import { VehicleChangedError } from "@/lib/errors";
import { createId } from "@/lib/ids";
import { DEFAULT_SHOT_TEMPLATE, getRequiredShots } from "@/lib/shots/shot-template";
import { saveVehicleData, savePhotoAndSyncStatus } from "./vehicle-workflow";

const T0 = "2026-10-28T09:00:00.000Z";
const T1 = "2026-10-28T10:00:00.000Z";
const T2 = "2026-10-28T11:00:00.000Z";

/** A vehicle whose 15 required photos were taken at T1 and all processed. */
async function processedVehicle() {
  const data = new MockDataProvider(createDemoDatabase(`workflow-${createId()}`));
  const vehicle = await data.createVehicle({
    manufacturer: "Audi",
    model: "A4",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  const capture = (shot: ReturnType<typeof getRequiredShots>[number], takenAt: string) => ({
    photoId: createId(),
    vehicleId: vehicle.id,
    shotKey: shot.key,
    shotOrder: shot.order,
    title: shot.title,
    file: new Blob(["jpeg"], { type: "image/jpeg" }),
    thumbnail: null,
    width: 4000,
    height: 3000,
    takenAt,
  });
  const shots = getRequiredShots(DEFAULT_SHOT_TEMPLATE);
  for (const shot of shots) {
    const { photo } = await savePhotoAndSyncStatus(data, DEFAULT_SHOT_TEMPLATE, capture(shot, T1));
    await data.saveProcessedPhoto({
      photo,
      preset: "autoexperten_standard",
      file: new Blob(["result"], { type: "image/jpeg" }),
      warnings: [],
    });
  }
  await data.setVehicleStatus(vehicle.id, "processed");
  const [firstShot] = shots;
  if (!firstShot) throw new Error("template without shots");
  return { data, vehicle, firstShot, capture };
}

describe("savePhotoAndSyncStatus", () => {
  it("keeps the vehicle processed when an older capture arrives late (stored as archived)", async () => {
    const { data, vehicle, firstShot, capture } = await processedVehicle();
    const before = (await data.listPhotos(vehicle.id)).find((photo) => photo.shotKey === firstShot.key);

    const { status } = await savePhotoAndSyncStatus(data, DEFAULT_SHOT_TEMPLATE, capture(firstShot, T0));

    expect(status).toBe("processed");
    expect((await data.getVehicle(vehicle.id))?.status).toBe("processed");
    const after = (await data.listPhotos(vehicle.id)).find((photo) => photo.shotKey === firstShot.key);
    expect(after?.id).toBe(before?.id);
    expect(after?.processedStoragePath).not.toBeNull();
  });

  it("resets a processed vehicle to complete for a newer retake", async () => {
    const { data, vehicle, firstShot, capture } = await processedVehicle();
    const retake = capture(firstShot, T2);

    const { status } = await savePhotoAndSyncStatus(data, DEFAULT_SHOT_TEMPLATE, retake);

    expect(status).toBe("complete");
    const after = (await data.listPhotos(vehicle.id)).find((photo) => photo.shotKey === firstShot.key);
    expect(after?.id).toBe(retake.photoId);
    expect(after?.processedStoragePath).toBeNull();
  });
});

describe("saveVehicleData ('Fahrzeugdaten')", () => {
  const INPUT: VehicleInput = {
    manufacturer: "Audi",
    model: "A4",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  };

  async function setup() {
    const data = new MockDataProvider(createDemoDatabase(`workflow-${createId()}`));
    const base = await data.createVehicle(INPUT);
    return { data, base };
  }

  it("saves the input on the data the form showed", async () => {
    const { data, base } = await setup();
    const saved = await saveVehicleData(data, base.id, { ...INPUT, color: "Rot" }, base);
    expect(saved.color).toBe("Rot");
    expect((await data.getVehicle(base.id))?.color).toBe("Rot");
  });

  it("never overwrites vehicle data changed elsewhere meanwhile – it reports the current vehicle", async () => {
    const { data, base } = await setup();
    await data.updateVehicle(base.id, { ...INPUT, notes: "Kollege: Delle Tür" });
    const error = await saveVehicleData(data, base.id, { ...INPUT, color: "Rot" }, base).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(VehicleChangedError);
    expect((error as VehicleChangedError).message).toBe(
      "Die Fahrzeugdaten wurden inzwischen geändert. Bitte prüfen und erneut speichern.",
    );
    expect((error as VehicleChangedError).current?.notes).toBe("Kollege: Delle Tür");
    const stored = await data.getVehicle(base.id);
    expect([stored?.notes, stored?.color]).toEqual(["Kollege: Delle Tür", null]);
  });

  it("saves over a change of the status alone (e.g. a photo saved in the background)", async () => {
    const { data, base } = await setup();
    await data.setVehicleStatus(base.id, "capturing");
    const update = vi.spyOn(data, "updateVehicle");
    const saved = await saveVehicleData(data, base.id, { ...INPUT, color: "Rot" }, base);
    expect([saved.color, saved.status]).toEqual(["Rot", "capturing"]);
    // Both attempts were conditional: the second on the vehicle with the new status.
    const [first, second, ...more] = update.mock.calls.map(([, , options]) => options?.expectedUpdatedAt);
    expect(first).toBe(base.updatedAt);
    expect(second).toBeDefined();
    expect(second).not.toBe(base.updatedAt);
    expect(more).toEqual([]);
  });

  it("is done when the same change was saved elsewhere already", async () => {
    const { data, base } = await setup();
    await data.updateVehicle(base.id, { ...INPUT, color: "Rot" });
    const saved = await saveVehicleData(data, base.id, { ...INPUT, color: "Rot" }, base);
    expect(saved.color).toBe("Rot");
  });
});
