import { describe, expect, it } from "vitest";
import type { ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T, getOrderedShots } from "@/lib/shots/shot-template";
import { getShotCheckStates } from "@/lib/processing/shot-check";
import { rejectionWarning } from "@/lib/processing/processing-warnings";
import {
  buildExportPlan,
  describeExportPlan,
  exportFileNameFor,
  exportItemLabel,
  exportOrderOf,
  formatShotNumbers,
  itemsToReview,
  reviewRows,
  selectExportItems,
  withOriginal,
  type ExportVehicle,
} from "./listing-export";

const VEHICLE: ExportVehicle = { id: "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301", internalReference: "FZ-2041" };
const TAKEN = "2026-11-05T09:00:00.000Z";
const CHECK_HINT: ProcessingWarning = {
  code: "vehicle_part_uncertain",
  message: "Ein Teil des Fahrzeugs ist unsicher – bitte das Ergebnis prüfen.",
};

function photo(
  shotKey: string,
  shotOrder: number,
  { processed = true, extension = "jpg", warnings = [] as ProcessingWarning[] } = {},
): VehiclePhotoWithUrls {
  return {
    id: `photo-${shotKey}`,
    vehicleId: VEHICLE.id,
    shotKey,
    shotOrder,
    title: shotKey,
    originalStoragePath: `${VEHICLE.id}/${shotKey}/photo-${shotKey}.${extension}`,
    processedStoragePath: processed ? `${VEHICLE.id}/autoexperten_standard/${shotKey}/photo-${shotKey}.jpg` : null,
    processedPreset: processed ? "autoexperten_standard" : null,
    processingWarnings: warnings,
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt: TAKEN,
    createdAt: TAKEN,
    updatedAt: TAKEN,
    urls: { original: "o", thumbnail: "t", processed: processed ? "p" : null },
  };
}

const SHOTS = getOrderedShots(T);

/** All 15 shots processed, with per-shot changes. */
function photos(changes: Record<string, Parameters<typeof photo>[2] | null> = {}) {
  return SHOTS.flatMap((shot) =>
    changes[shot.key] === null ? [] : [photo(shot.key, shot.order, changes[shot.key] ?? {})],
  );
}

function plan(
  list: VehiclePhotoWithUrls[],
  { checks = new Map<string, ShotCheckState>(), pending = [] as UploadItem[], vehicle = VEHICLE } = {},
) {
  return buildExportPlan({ template: T, vehicle, slots: buildPhotoSlots(T, list, pending), checks });
}

describe("buildExportPlan", () => {
  it("takes the processed result of every shot, in template order, named AE_{ref}_{nn}_{shot}.jpg", () => {
    // Saved in reverse order – the export follows the template, never the time.
    const result = plan(photos().reverse());
    expect(result.complete).toBe(true);
    expect(describeExportPlan(result)).toBe("15 Fotos bereit");
    const selected = selectExportItems(result);
    expect(selected.map((item) => item.fileName)).toEqual(
      SHOTS.map((shot) => `AE_FZ-2041_${String(shot.order).padStart(2, "0")}_${shot.key}.jpg`),
    );
    expect(selected.every((item) => item.version === "processed")).toBe(true);
    expect(selected[0]?.fileName).toBe("AE_FZ-2041_01_front_left_45.jpg");
    expect(selected[14]?.fileName).toBe("AE_FZ-2041_15_special_detail.jpg");
    expect(itemsToReview(result)).toEqual([]);
  });

  it("falls back to the original (marked) for an unprocessed shot, keeping its own file type", () => {
    const result = plan(photos({ cockpit: { processed: false, extension: "heic" } }));
    const cockpit = result.items.find((item) => item.key === "cockpit");
    expect(cockpit).toMatchObject({
      status: "original",
      version: "original",
      fileName: "AE_FZ-2041_09_cockpit.heic",
    });
    expect(exportItemLabel(cockpit!)).toBe("Noch nicht bearbeitet – das Original wird gespeichert");
    expect(result.complete).toBe(false);
    expect(describeExportPlan(result)).toBe("14 bearbeitet · 1 noch nicht bearbeitet");
    expect(selectExportItems(result)).toHaveLength(15);
  });

  it("leaves out rejected shots unless the employee confirms them", () => {
    const checks = new Map<string, ShotCheckState>([
      ["rear", { kind: "retake", reason: "Das Fahrzeug ist angeschnitten." }],
      ["left_side", { kind: "retake", reason: "Fremdes Objekt an der Kontur." }],
    ]);
    const result = plan(photos({ rear: { processed: false }, rear_seats: { processed: false } }), { checks });
    expect(describeExportPlan(result)).toBe("12 bearbeitet · 1 noch nicht bearbeitet · 2 müssen neu aufgenommen werden");

    const without = selectExportItems(result);
    expect(without.map((item) => item.key)).not.toContain("rear");
    expect(without.map((item) => item.key)).not.toContain("left_side");
    expect(without).toHaveLength(13);

    const confirmed = selectExportItems(result, { includeRetakes: true });
    expect(confirmed).toHaveLength(15);
    // A rejected shot with a processed result exports that, else its original.
    expect(confirmed.find((item) => item.key === "left_side")?.version).toBe("processed");
    expect(confirmed.find((item) => item.key === "rear")?.version).toBe("original");
    expect(confirmed.map((item) => item.order)).toEqual(SHOTS.map((shot) => shot.order));
    expect(result.items.find((item) => item.key === "rear")?.note).toBe("Das Fahrzeug ist angeschnitten.");
  });

  describe("a rejection kept with the photo (known on every device)", () => {
    const REJECTED = rejectionWarning(
      "vehicle_cropped",
      "Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren.",
    );

    it("is a retake without any check of this device – left out unless confirmed", () => {
      // Another device: no checks at all, only what is saved with the photos.
      const list = photos({ rear: { processed: false, warnings: [REJECTED] }, cockpit: { processed: false } });
      const result = buildExportPlan({ template: T, vehicle: VEHICLE, slots: buildPhotoSlots(T, list) });
      expect(describeExportPlan(result)).toBe("13 bearbeitet · 1 noch nicht bearbeitet · 1 muss neu aufgenommen werden");
      expect(result.items.find((item) => item.key === "rear")).toMatchObject({
        status: "retake",
        version: "original",
        note: REJECTED.message,
      });
      expect(selectExportItems(result).map((item) => item.key)).not.toContain("rear");
      expect(selectExportItems(result, { includeRetakes: true }).map((item) => item.key)).toContain("rear");
    });

    it("is read the same way through the shot check states the vehicle page passes", () => {
      const list = photos({ rear: { processed: false, warnings: [REJECTED] } });
      const slots = buildPhotoSlots(T, list);
      // A fresh device: no checks, checks even off.
      const checks = getShotCheckStates([...slots.required, ...slots.extras], VEHICLE.id, {
        availability: "no_processor",
        checks: new Map(),
        captures: new Map(),
      });
      expect(checks.get("rear")).toEqual({ kind: "retake", reason: REJECTED.message });
      const result = buildExportPlan({ template: T, vehicle: VEHICLE, slots, checks });
      expect(result.items.find((item) => item.key === "rear")?.status).toBe("retake");
    });

    it("also covers a result whose hint says the photo itself is the problem", () => {
      const mirrored = { code: "plate_mirrored", message: "Das Foto zeigt das Fahrzeug von der anderen Seite." };
      const list = photos({ front_right_45: { warnings: [mirrored] } });
      const result = buildExportPlan({ template: T, vehicle: VEHICLE, slots: buildPhotoSlots(T, list) });
      expect(result.items.find((item) => item.key === "front_right_45")).toMatchObject({
        status: "retake",
        version: "processed",
        note: mirrored.message,
      });
    });

    it("a newer check of this device wins (e.g. the photo is being checked again)", () => {
      const list = photos({ rear: { processed: false, warnings: [REJECTED] } });
      const checks = new Map<string, ShotCheckState>([["rear", { kind: "checking" }]]);
      const result = plan(list, { checks });
      expect(result.items.find((item) => item.key === "rear")).toMatchObject({
        status: "original",
        note: "Wird gerade geprüft.",
      });
    });
  });

  it("leaves out shots the employee chose to leave out, and uses the original where chosen", () => {
    const result = plan(photos({ front: { extension: "png" } }));
    const selected = selectExportItems(result, { leaveOut: new Set(["cockpit"]), useOriginal: new Set(["front"]) });
    expect(selected).toHaveLength(14);
    expect(selected.map((item) => item.key)).not.toContain("cockpit");
    expect(selected.find((item) => item.key === "front")).toMatchObject({
      version: "original",
      fileName: "AE_FZ-2041_02_front.png",
      originalInstead: true,
    });
    // Every other shot keeps its processed result, in shot order.
    expect(selected.filter((item) => item.version === "processed")).toHaveLength(13);
    expect(selected.map((item) => item.order)).toEqual(SHOTS.filter((shot) => shot.key !== "cockpit").map((s) => s.order));
  });

  it("withOriginal changes only items that export a processed result", () => {
    const result = plan(photos({ cockpit: { processed: false } }));
    const cockpit = result.items.find((item) => item.key === "cockpit")!;
    expect(withOriginal(cockpit)).toBe(cockpit);
    const front = result.items.find((item) => item.key === "front")!;
    expect(front.originalFileName).toBe("AE_FZ-2041_02_front.jpg");
    expect(withOriginal(front)).toMatchObject({ version: "original", fileName: "AE_FZ-2041_02_front.jpg" });
  });

  it("matches the example of a mixed set", () => {
    const checks = new Map<string, ShotCheckState>([["front", { kind: "retake", reason: "Bitte quer halten." }]]);
    const result = plan(photos({ cockpit: { processed: false } }), { checks });
    expect(describeExportPlan(result)).toBe("13 bearbeitet · 1 noch nicht bearbeitet · 1 muss neu aufgenommen werden");
    expect(itemsToReview(result).map((item) => [item.key, exportItemLabel(item)])).toEqual([
      ["front", "Muss neu aufgenommen werden"],
      ["cockpit", "Noch nicht bearbeitet – das Original wird gespeichert"],
    ]);
  });

  it("counts missing shots and captures still being saved – the photo they replace is not exported", () => {
    const pending: UploadItem[] = [
      {
        id: "u1",
        vehicleId: VEHICLE.id,
        shotKey: "front",
        shotOrder: 2,
        title: "Vorne",
        takenAt: "2026-11-05T10:00:00.000Z",
        status: "failed",
        thumbnailUrl: null,
        error: "Foto konnte nicht gespeichert werden.",
        retryable: true,
      },
    ];
    const result = plan(photos({ rear: null, rear_seats: null }), { pending });
    expect(describeExportPlan(result)).toBe("12 bearbeitet · 2 fehlen · 1 noch nicht gespeichert");
    expect(result.items.find((item) => item.key === "front")).toMatchObject({ status: "unsaved", version: null, fileName: null });
    expect(result.items.find((item) => item.key === "rear")).toMatchObject({ status: "missing", version: null });
    expect(selectExportItems(result, { includeRetakes: true })).toHaveLength(12);
  });

  it("flags processed results the processor asks to check – they are exported", () => {
    const result = plan(photos({ left_side: { warnings: [CHECK_HINT] } }));
    expect(result.complete).toBe(true);
    expect(result.counts.check).toBe(1);
    const side = result.items.find((item) => item.key === "left_side");
    expect(side).toMatchObject({ status: "processed", needsCheck: true, note: CHECK_HINT.message });
    expect(itemsToReview(result).map((item) => item.key)).toEqual(["left_side"]);
    expect(selectExportItems(result)).toHaveLength(15);
  });

  it("adds extra photos as originals after the template shots, numbered 16, 17, …", () => {
    const extras = [
      { ...photo("extra_01", 101, { processed: false }), title: "Zusatzfoto 1" },
      { ...photo("extra_02", 102, { processed: false, extension: "png" }), title: "Zusatzfoto 2" },
    ];
    const result = plan([...photos(), ...extras]);
    expect(describeExportPlan(result)).toBe("15 Fotos bereit · 2 Zusatzfotos");
    const names = selectExportItems(result).map((item) => item.fileName ?? "");
    expect(names.slice(-2)).toEqual(["AE_FZ-2041_16_extra_01.jpg", "AE_FZ-2041_17_extra_02.png"]);
    expect(selectExportItems(result).at(-1)).toMatchObject({ key: "extra_02", status: "extra", version: "original", order: 17 });
    // Plain name order (as file pickers sort) is the listing order.
    expect([...names].sort()).toEqual(names);
  });

  it("names files by the vehicle id when there is no internal reference", () => {
    const result = plan(photos(), { vehicle: { id: VEHICLE.id, internalReference: null } });
    expect(selectExportItems(result)[0]?.fileName).toBe("AE_5D0C6F1E_01_front_left_45.jpg");
  });
});

describe("exportFileNameFor", () => {
  it("names a processed result .jpg and an original by its own type", () => {
    const front = photo("front", 2, { extension: "png" });
    expect(exportFileNameFor(T, VEHICLE, front, "processed")).toBe("AE_FZ-2041_02_front.jpg");
    expect(exportFileNameFor(T, VEHICLE, front, "original")).toBe("AE_FZ-2041_02_front.png");
    expect(exportFileNameFor(T, VEHICLE, photo("front", 2, { extension: "jpeg" }), "original")).toBe(
      "AE_FZ-2041_02_front.jpg",
    );
  });

  it("numbers an extra photo after the template – the same name as in the whole set", () => {
    expect(exportFileNameFor(T, VEHICLE, photo("extra_03", 103, { processed: false }), "original")).toBe(
      "AE_FZ-2041_18_extra_03.jpg",
    );
  });
});

describe("exportOrderOf", () => {
  it("keeps the template's order and puts extra photos right after it", () => {
    expect(exportOrderOf(T, { shotKey: "front_left_45", shotOrder: 1 })).toBe(1);
    expect(exportOrderOf(T, { shotKey: "special_detail", shotOrder: 15 })).toBe(15);
    expect(exportOrderOf(T, { shotKey: "extra_01", shotOrder: 101 })).toBe(16);
    expect(exportOrderOf(T, { shotKey: "extra_12", shotOrder: 112 })).toBe(27);
  });
});

describe("reviewRows", () => {
  it("groups shots that are only not processed yet, keeps rejected shots on their own rows", () => {
    const checks = new Map<string, ShotCheckState>([["rear", { kind: "retake", reason: "Angeschnitten." }]]);
    const unprocessed = Object.fromEntries(
      ["front_left_45", "front", "front_right_45", "cockpit"].map((key) => [key, { processed: false }]),
    );
    const result = plan(photos(unprocessed), { checks });
    expect(describeExportPlan(result)).toBe("10 bearbeitet · 4 noch nicht bearbeitet · 1 muss neu aufgenommen werden");
    expect(reviewRows(result).map((row) => [row.numbers, row.title, row.label, row.shotKey])).toEqual([
      ["01–03, 09", "4 Fotos", "Noch nicht bearbeitet – die Originale werden gespeichert", null],
      ["07", "Hinten", "Muss neu aufgenommen werden", "rear"],
    ]);
  });

  it("lists up to two shots of a status one by one", () => {
    const result = plan(photos({ cockpit: { processed: false }, front: { processed: false } }));
    expect(reviewRows(result).map((row) => row.numbers)).toEqual(["02", "09"]);
  });

  it("says '15 noch nicht bearbeitet' – without '0 bearbeitet' – when nothing is processed", () => {
    const none = Object.fromEntries(SHOTS.map((shot) => [shot.key, { processed: false }]));
    const result = plan(photos(none));
    expect(describeExportPlan(result)).toBe("15 noch nicht bearbeitet");
    expect(reviewRows(result)).toHaveLength(1);
    expect(reviewRows(result)[0]?.numbers).toBe("01–15");
  });
});

describe("formatShotNumbers", () => {
  it("writes runs of three or more as ranges", () => {
    expect(formatShotNumbers([1, 2, 3, 5, 7, 8, 12, 13, 14, 15])).toBe("01–03, 05, 07, 08, 12–15");
    expect(formatShotNumbers([9])).toBe("09");
    expect(formatShotNumbers([])).toBe("");
  });
});

