import { describe, expect, it } from "vitest";
import type { ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T, getOrderedShots } from "@/lib/shots/shot-template";
import {
  buildExportPlan,
  describeExportPlan,
  exportableItems,
  exportFileNameFor,
  exportItemLabel,
  itemsToReview,
  selectExportItems,
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
    expect(exportableItems(result)).toHaveLength(15);
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

  it("adds extra photos as originals after the template shots", () => {
    const extra = { ...photo("extra_01", 101, { processed: false }), title: "Zusatzfoto 1" };
    const result = plan([...photos(), extra]);
    expect(describeExportPlan(result)).toBe("15 Fotos bereit · 1 Zusatzfoto");
    const last = selectExportItems(result).at(-1);
    expect(last).toMatchObject({ key: "extra_01", status: "extra", version: "original" });
    expect(last?.fileName).toBe("AE_FZ-2041_101_extra_01.jpg");
  });

  it("names files by the vehicle id when there is no internal reference", () => {
    const result = plan(photos(), { vehicle: { id: VEHICLE.id, internalReference: null } });
    expect(selectExportItems(result)[0]?.fileName).toBe("AE_5D0C6F1E_01_front_left_45.jpg");
  });
});

describe("exportFileNameFor", () => {
  it("names a processed result .jpg and an original by its own type", () => {
    const front = photo("front", 2, { extension: "png" });
    expect(exportFileNameFor(VEHICLE, front, "processed")).toBe("AE_FZ-2041_02_front.jpg");
    expect(exportFileNameFor(VEHICLE, front, "original")).toBe("AE_FZ-2041_02_front.png");
    expect(exportFileNameFor(VEHICLE, photo("front", 2, { extension: "jpeg" }), "original")).toBe(
      "AE_FZ-2041_02_front.jpg",
    );
  });
});
