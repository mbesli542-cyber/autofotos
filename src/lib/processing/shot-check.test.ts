import { describe, expect, it } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { UploadItem } from "@/lib/offline/upload-queue";
import type { PhotoCheck } from "./photo-checks";
import {
  ACCESS_CODE_INVALID_CODE,
  autoCheckOffMessage,
  checkResultMessage,
  getShotCheckState,
  getShotCheckStates,
} from "./shot-check";

const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";
const MIRRORED = { code: "plate_mirrored", message: "Das Foto zeigt das Fahrzeug von der anderen Seite." };
const UNCERTAIN = { code: "vehicle_part_uncertain", message: "Ein Fahrzeugteil ist unsicher – bitte prüfen." };

function photo(id: string, takenAt = "2026-11-03T10:00:00.000Z", patch: Partial<VehiclePhotoWithUrls> = {}): VehiclePhotoWithUrls {
  return {
    id,
    vehicleId: "veh_1",
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    originalStoragePath: `veh_1/front/${id}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt,
    createdAt: takenAt,
    updatedAt: takenAt,
    urls: { original: "", thumbnail: "", processed: null },
    ...patch,
  };
}

function check(photoId: string, patch: Partial<PhotoCheck> = {}): PhotoCheck {
  return {
    photoId,
    vehicleId: "veh_1",
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    takenAt: "2026-11-03T10:00:00.000Z",
    preset: "autoexperten_standard",
    status: "ok",
    message: null,
    errorCode: null,
    warnings: [],
    ...patch,
  };
}

function pending(status: UploadItem["status"], takenAt = "2026-11-03T10:05:00.000Z"): UploadItem {
  return {
    id: "u_new",
    vehicleId: "veh_1",
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    takenAt,
    status,
    thumbnailUrl: null,
    error: null,
    retryable: true,
  };
}

const slot = (p: VehiclePhotoWithUrls | null, upload: UploadItem | null = null, category: "exterior" | null = "exterior") => ({
  key: "front",
  category,
  photo: p,
  pending: upload,
});

describe("getShotCheckState", () => {
  it("follows the check of the shot's photo", () => {
    const current = photo("p1");
    expect(getShotCheckState(slot(current), [check("p1", { status: "waiting" })], "on")).toEqual({ kind: "checking" });
    expect(getShotCheckState(slot(current), [check("p1", { status: "checking" })], "on")).toEqual({ kind: "checking" });
    expect(getShotCheckState(slot(current), [check("p1")], "on")).toEqual({ kind: "ok", hint: null });
    expect(getShotCheckState(slot(current), [check("p1", { warnings: [UNCERTAIN] })], "on")).toEqual({
      kind: "ok",
      hint: UNCERTAIN.message,
    });
    expect(
      getShotCheckState(slot(current), [check("p1", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" })], "on"),
    ).toEqual({ kind: "retake", reason: TOO_SMALL });
    // A result whose hint says the photo itself is the problem.
    expect(getShotCheckState(slot(current), [check("p1", { status: "retake", message: MIRRORED.message, warnings: [MIRRORED] })], "on")).toEqual({
      kind: "retake",
      reason: MIRRORED.message,
    });
  });

  it("shows a newer capture as being checked – never the result of the photo it replaces", () => {
    const old = photo("p1");
    const rejected = check("p1", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" });
    expect(getShotCheckState(slot(old, pending("uploading")), [rejected], "on")).toEqual({ kind: "checking" });
    // Checks off: nothing is checked, so nothing is claimed.
    expect(getShotCheckState(slot(old, pending("queued")), [rejected], "no_processor")).toEqual({ kind: "none" });
    expect(getShotCheckState(slot(old, pending("failed")), [rejected], "on")).toEqual({ kind: "none" });
    // Saved a moment ago, the photo list is not reloaded yet: the new photo's check counts.
    const newer = check("p2", { status: "checking", takenAt: "2026-11-03T10:05:00.000Z" });
    expect(getShotCheckState(slot(old), [rejected, newer], "on")).toEqual({ kind: "checking" });
    // A late check of an older, archived capture never counts.
    const archived = check("p0", { status: "retake", message: TOO_SMALL, errorCode: "x", takenAt: "2026-11-03T09:00:00.000Z" });
    expect(getShotCheckState(slot(photo("p1")), [archived], "on")).toEqual({ kind: "none" });
  });

  it("uses the saved result when there is no check", () => {
    const processed = photo("p1", undefined, { processedStoragePath: "x.jpg", processedPreset: "autoexperten_standard" });
    expect(getShotCheckState(slot(processed), [], "unknown")).toEqual({ kind: "ok", hint: null });
    expect(getShotCheckState(slot({ ...processed, processingWarnings: [MIRRORED] }), [], "on")).toEqual({
      kind: "retake",
      reason: MIRRORED.message,
    });
    // A check that could not run does not hide a result saved before.
    expect(getShotCheckState(slot(processed), [check("p1", { status: "error", message: "Offline" })], "on")).toEqual({
      kind: "ok",
      hint: null,
    });
    expect(getShotCheckState(slot(photo("p1")), [check("p1", { status: "error", message: "Offline" })], "on")).toEqual({
      kind: "error",
      reason: "Offline",
    });
    expect(getShotCheckState(slot(photo("p1")), [], "on")).toEqual({ kind: "none" });
    expect(getShotCheckState(slot(null), [], "on")).toEqual({ kind: "none" });
  });

  it("never shows the result of a replaced photo still on the list – also with the checks off", () => {
    const old = photo("p_old", "2026-11-03T10:00:00.000Z", {
      processedStoragePath: "x.jpg",
      processedPreset: "autoexperten_standard",
    });
    const newer = { photoId: "p_new", takenAt: "2026-11-03T10:05:00.000Z" };
    // The retake is saved, its pending item is gone, the photo list still shows the old photo.
    expect(getShotCheckState(slot(old), [], "on", newer)).toEqual({ kind: "none" });
    expect(getShotCheckState(slot(old), [check("p_new", { status: "waiting", takenAt: newer.takenAt })], "no_processor", newer)).toEqual({
      kind: "none",
    });
    expect(getShotCheckState(slot(old), [check("p_new", { status: "waiting", takenAt: newer.takenAt })], "on", newer)).toEqual({
      kind: "checking",
    });
    // The newest capture is the photo on the list: its result counts.
    expect(getShotCheckState(slot(old), [], "on", { photoId: "p_old", takenAt: old.takenAt })).toEqual({ kind: "ok", hint: null });
    // Through the snapshot (vehicleId/shotKey).
    const states = getShotCheckStates([{ ...slot(old) }], "veh_1", {
      availability: "on",
      checks: new Map(),
      captures: new Map([["veh_1/front", newer]]),
    });
    expect(states.get("front")).toEqual({ kind: "none" });
  });

  it("tells a wrong access code apart", () => {
    expect(
      getShotCheckState(
        slot(photo("p1")),
        [check("p1", { status: "error", message: "Falsch", errorCode: ACCESS_CODE_INVALID_CODE })],
        "access_code",
      ),
    ).toEqual({ kind: "error", reason: "Falsch", accessCode: true });
  });

  it("never shows a state for extra photos", () => {
    expect(getShotCheckState(slot(photo("p1"), null, null), [check("p1")], "on")).toEqual({ kind: "none" });
  });
});

describe("checkResultMessage", () => {
  it("says in one line whether the shot is usable", () => {
    expect(checkResultMessage({ order: 2, title: "Vorne" }, { kind: "ok", hint: null })).toBe("02 Vorne passt ✓");
    expect(checkResultMessage({ order: 1, title: "Vorne links (45°)" }, { kind: "retake", reason: TOO_SMALL })).toBe(
      `01 Vorne links (45°): bitte neu aufnehmen – ${TOO_SMALL}`,
    );
    expect(checkResultMessage({ order: 3, title: "Vorne rechts (45°)" }, { kind: "ok", hint: UNCERTAIN.message })).toBe(
      `03 Vorne rechts (45°) passt – bitte Ergebnis später prüfen: ${UNCERTAIN.message}`,
    );
    expect(checkResultMessage({ order: 2, title: "Vorne" }, { kind: "checking" })).toBeNull();
    expect(checkResultMessage({ order: 7, title: "Hinten" }, { kind: "error", reason: "x", accessCode: true })).toBe(
      "07 Hinten: nicht geprüft – der Zugangscode ist falsch. Bitte neu eingeben.",
    );
  });

  it("explains why the check is off (and how to switch it on in demo mode)", () => {
    expect(autoCheckOffMessage("on")).toBeNull();
    expect(autoCheckOffMessage("unknown")).toBeNull();
    expect(autoCheckOffMessage("access_code")).toContain("Zugangscode");
    expect(autoCheckOffMessage("no_processor")).toContain("nicht verbunden");
    expect(autoCheckOffMessage("offline")).toContain("Keine Internetverbindung");
  });
});
