/** Status sync of savePhotoAndSyncStatus on the demo provider (in-memory IndexedDB fallback). */
import { describe, expect, it, vi } from "vitest";
import { createDemoDatabase, MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import type { VehicleInput } from "@/lib/domain/types";
import { DuplicateReferenceError, toUserMessage, VehicleChangedError } from "@/lib/errors";
import { createId } from "@/lib/ids";
import { DEFAULT_SHOT_TEMPLATE, getRequiredShots } from "@/lib/shots/shot-template";
import { createVehicleWithUniqueReference, saveVehicleData, savePhotoAndSyncStatus } from "./vehicle-workflow";

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

describe("'Interne Fahrzeugnummer' stays unique (export file names)", () => {
  const INPUT: VehicleInput = {
    manufacturer: "Mercedes-Benz",
    model: "C 200",
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
    // The demo seed has FZ-2041 (S 63 AMG).
    const taken = (await data.listVehicleReferences()).find((entry) => entry.internalReference === "FZ-2041");
    if (!taken) throw new Error("seed without FZ-2041");
    return { data, taken };
  }

  it("refuses a new vehicle with a number another vehicle has – also spelt differently", async () => {
    const { data, taken } = await setup();
    const before = (await data.listVehicles()).length;
    for (const reference of ["FZ-2041", "fz 2041"]) {
      const error = await createVehicleWithUniqueReference(data, { ...INPUT, internalReference: reference }).catch(
        (reason: unknown) => reason,
      );
      expect(error).toBeInstanceOf(DuplicateReferenceError);
      expect(toUserMessage(error)).toBe("Diese Fahrzeugnummer ist schon vergeben.");
      expect((error as DuplicateReferenceError).vehicleId).toBe(taken.id);
    }
    expect((await data.listVehicles()).length).toBe(before);
  });

  it("creates vehicles with a new number or without one", async () => {
    const { data } = await setup();
    await createVehicleWithUniqueReference(data, { ...INPUT, internalReference: "FZ-3001" });
    await createVehicleWithUniqueReference(data, INPUT);
    await createVehicleWithUniqueReference(data, INPUT);
    const references = (await data.listVehicleReferences()).map((entry) => entry.internalReference);
    expect(references).toContain("FZ-3001");
  });

  it("refuses to change a vehicle to a taken number, but saves it with its own", async () => {
    const { data } = await setup();
    const base = await data.createVehicle({ ...INPUT, internalReference: "FZ-3002" });
    const error = await saveVehicleData(data, base.id, { ...INPUT, internalReference: "FZ-2041" }, base).catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(DuplicateReferenceError);
    expect((await data.getVehicle(base.id))?.internalReference).toBe("FZ-3002");

    const saved = await saveVehicleData(data, base.id, { ...INPUT, internalReference: "FZ-3002", color: "Blau" }, base);
    expect([saved.internalReference, saved.color]).toEqual(["FZ-3002", "Blau"]);
  });

  it("keeps a vehicle editable that already shares its number (data from before the rule)", async () => {
    const { data } = await setup();
    // A duplicate saved before the rule existed (bypassing it).
    const base = await data.createVehicle({ ...INPUT, internalReference: "FZ-2041" });
    const listReferences = vi.spyOn(data, "listVehicleReferences");
    const saved = await saveVehicleData(data, base.id, { ...INPUT, internalReference: "FZ-2041", mileage: 42000 }, base);
    expect([saved.internalReference, saved.mileage]).toEqual(["FZ-2041", 42000]);
    // The same number spelt differently is no change either.
    await saveVehicleData(data, base.id, { ...INPUT, internalReference: "fz 2041", mileage: 42001 }, saved);
    expect(listReferences).not.toHaveBeenCalled();

    // Changing it to another taken number is still refused.
    await data.createVehicle({ ...INPUT, internalReference: "FZ-3003" });
    const current = await data.getVehicle(base.id);
    const error = await saveVehicleData(data, base.id, { ...INPUT, internalReference: "FZ-3003" }, current!).catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(DuplicateReferenceError);
  });
});
