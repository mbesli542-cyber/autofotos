import { describe, expect, it } from "vitest";
import type { VehiclePhoto } from "@/lib/domain/types";
import { DEFAULT_SHOT_TEMPLATE } from "@/lib/shots/shot-template";
import type { PhotoCheck, PhotoChecksSnapshot } from "./photo-checks";
import {
  completeWithRetakesQuestion,
  countRetakeShots,
  needsCamera,
  retakeCountLabel,
  showsStatusBadge,
  vehicleRetakeShotKeys,
} from "./retake-summary";
import type { ShotCheckState } from "./shot-check";

const T = "2026-11-05T10:00:00.000Z";

function photo(shotKey: string, overrides: Partial<VehiclePhoto> = {}): VehiclePhoto {
  return {
    id: `photo-${shotKey}`,
    vehicleId: "veh-1",
    shotKey,
    shotOrder: 1,
    title: shotKey,
    originalStoragePath: `vehicle-originals/veh-1/${shotKey}/photo-${shotKey}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt: T,
    createdAt: T,
    updatedAt: T,
    ...overrides,
  };
}

function rejection(shotKey: string, overrides: Partial<PhotoCheck> = {}): PhotoCheck {
  return {
    photoId: `photo-${shotKey}`,
    vehicleId: "veh-1",
    shotKey,
    shotOrder: 1,
    title: shotKey,
    takenAt: T,
    preset: "autoexperten_standard",
    status: "retake",
    message: "Das Fahrzeug ist im Foto angeschnitten.",
    errorCode: "vehicle_cropped",
    warnings: [],
    restored: true,
    ...overrides,
  };
}

function snapshot(checks: PhotoCheck[]): PhotoChecksSnapshot {
  return {
    availability: "unknown",
    checks: new Map(checks.map((check) => [check.photoId, check])),
    captures: new Map(),
  };
}

describe("countRetakeShots", () => {
  it("counts only the shots to retake (not errors, hints or checks still running)", () => {
    const states: ShotCheckState[] = [
      { kind: "retake", reason: "a" },
      { kind: "ok", hint: "bitte prüfen" },
      { kind: "error", reason: "offline" },
      { kind: "checking" },
      { kind: "none" },
      { kind: "retake", reason: "b" },
    ];
    expect(countRetakeShots(states)).toBe(2);
    expect(countRetakeShots([])).toBe(0);
  });
});

describe("texts", () => {
  it("uses the correct plural", () => {
    expect(retakeCountLabel(1)).toBe("1 neu aufnehmen");
    expect(completeWithRetakesQuestion(1)).toBe("1 Foto muss neu aufgenommen werden. Trotzdem abschließen?");
    expect(completeWithRetakesQuestion(3)).toBe("3 Fotos müssen neu aufgenommen werden. Trotzdem abschließen?");
  });
});

describe("vehicleRetakeShotKeys (vehicle lists, Kamera tab)", () => {
  const photos = [photo("front_left_45"), photo("front"), photo("cockpit")];

  it("lists a stored rejection of the shot's current photo", () => {
    expect(vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", photos, snapshot([rejection("front")]))).toEqual(["front"]);
  });

  it("lists a saved result whose hint asks for a retake – in template order", () => {
    const withHint = photo("front_left_45", {
      processedStoragePath: "vehicle-processed/veh-1/autoexperten_standard/front_left_45/photo-front_left_45.jpg",
      processedPreset: "autoexperten_standard",
      processingWarnings: [{ code: "plate_mirrored", message: "Das Foto zeigt die andere Fahrzeugseite." }],
    });
    expect(
      vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", [photo("cockpit"), photo("front"), withHint], snapshot([rejection("front")])),
    ).toEqual(["front_left_45", "front"]);
  });

  it("ignores rejections of another vehicle and of a photo a retake replaced", () => {
    const other = rejection("front", { vehicleId: "veh-2" });
    const replaced = rejection("front_left_45", {
      photoId: "photo-old",
      takenAt: "2026-11-05T09:00:00.000Z",
    });
    expect(vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", photos, snapshot([other, replaced]))).toEqual([]);
  });

  it("is empty without photos", () => {
    expect(vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", [], snapshot([rejection("front")]))).toEqual([]);
  });

  it("lists a rejection kept with the photo (checked on another device) – the same rule as every screen", () => {
    const rejectedElsewhere = photo("front", {
      processingWarnings: [{ code: "rejected:vehicle_cropped", message: "Das Fahrzeug ist angeschnitten." }],
    });
    expect(vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", [rejectedElsewhere], snapshot([]))).toEqual(["front"]);
    // A result saved later (e.g. "Fotos bearbeiten" on another device) wins over this device's old rejection.
    const processed = photo("front", {
      processedStoragePath: "vehicle-processed/veh-1/autoexperten_standard/front/photo-front.jpg",
      processedPreset: "autoexperten_standard",
    });
    expect(vehicleRetakeShotKeys(DEFAULT_SHOT_TEMPLATE, "veh-1", [processed], snapshot([rejection("front")]))).toEqual([]);
  });
});

describe("needsCamera (Kamera tab)", () => {
  it("keeps a vehicle while a shot is missing or red – a complete set without red shots is done", () => {
    expect(needsCamera({ capturedRequiredCount: 14, requiredCount: 15 }, [])).toBe(true);
    expect(needsCamera({ capturedRequiredCount: 15, requiredCount: 15 }, ["front"])).toBe(true);
    expect(needsCamera({ capturedRequiredCount: 15, requiredCount: 15 }, [])).toBe(false);
  });
});

describe("showsStatusBadge", () => {
  it("never shows 'Vollständig' next to a shot to retake", () => {
    expect(showsStatusBadge("complete", 1)).toBe(false);
    expect(showsStatusBadge("complete", 0)).toBe(true);
    for (const status of ["new", "capturing", "processed"] as const) expect(showsStatusBadge(status, 2)).toBe(true);
  });
});
