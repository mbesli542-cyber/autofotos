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
  passThroughState,
  resultsToCheckMessage,
  retakesLeftMessage,
  shotCheckDescription,
  shotCheckDetail,
  shotCheckLabel,
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

  it("shows a rejection kept with the photo on every device – also with the checks off", () => {
    const rejected = photo("p1", undefined, { processingWarnings: [{ code: "rejected:vehicle_too_small", message: TOO_SMALL }] });
    // Another device: no check of this photo at all.
    expect(getShotCheckState(slot(rejected), [], "no_processor")).toEqual({ kind: "retake", reason: TOO_SMALL });
    // A check that could not run does not hide it either.
    expect(getShotCheckState(slot(rejected), [check("p1", { status: "error", message: "Offline" })], "on")).toEqual({
      kind: "retake",
      reason: TOO_SMALL,
    });
    // A newer check of this device wins (checked again right now, or passed).
    expect(getShotCheckState(slot(rejected), [check("p1", { status: "checking" })], "on")).toEqual({ kind: "checking" });
    // A retake saved meanwhile: the rejection of the photo it replaced no longer counts.
    const newer = { photoId: "p2", takenAt: "2026-11-03T10:05:00.000Z" };
    expect(getShotCheckState(slot(rejected), [], "on", newer)).toEqual({ kind: "none" });
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
  it("names the shot and its state with the one wording of every screen, the reason after it", () => {
    expect(checkResultMessage({ order: 2, title: "Vorne" }, { kind: "ok", hint: null })).toEqual({
      label: "02 Vorne · Geprüft – passt",
      detail: null,
    });
    expect(checkResultMessage({ order: 1, title: "Vorne links (45°)" }, { kind: "retake", reason: TOO_SMALL })).toEqual({
      label: "01 Vorne links (45°) · Bitte neu aufnehmen",
      detail: TOO_SMALL,
    });
    expect(checkResultMessage({ order: 3, title: "Vorne rechts (45°)" }, { kind: "ok", hint: UNCERTAIN.message })).toEqual({
      label: "03 Vorne rechts (45°) · Geprüft – bitte Ergebnis ansehen",
      detail: UNCERTAIN.message,
    });
    expect(checkResultMessage({ order: 2, title: "Vorne" }, { kind: "checking" })).toBeNull();
    expect(checkResultMessage({ order: 7, title: "Hinten" }, { kind: "error", reason: "x", accessCode: true })).toEqual({
      label: "07 Hinten · Prüfung nicht möglich",
      detail: "Der Zugangscode ist falsch – bitte neu eingeben.",
    });
    expect(checkResultMessage({ order: 7, title: "Hinten" }, { kind: "error", reason: "x" })).toEqual({
      label: "07 Hinten · Prüfung nicht möglich",
      detail: "Wird unter „Fotos bearbeiten“ nachgeholt.",
    });
  });

  it("explains why the check is off (and how to switch it on in demo mode)", () => {
    expect(autoCheckOffMessage("on")).toBeNull();
    expect(autoCheckOffMessage("unknown")).toBeNull();
    expect(autoCheckOffMessage("access_code")).toContain("Zugangscode");
    expect(autoCheckOffMessage("no_processor")).toContain("noch nicht verbunden");
    // Configured but not answering: never "nicht verbunden".
    expect(autoCheckOffMessage("unreachable")).toMatch(/^Der Showroom-Rechner ist gerade nicht erreichbar\./);
    expect(autoCheckOffMessage("unreachable")).not.toContain("verbunden");
    expect(autoCheckOffMessage("offline")).toContain("Keine Internetverbindung");
  });
});

describe("interior and detail shots (passed through, never checked)", () => {
  const interior = (p: VehiclePhotoWithUrls | null, upload: UploadItem | null = null, category: "interior" | "detail" = "interior") => ({
    key: "front",
    category,
    photo: p,
    pending: upload,
  });

  it("are saved – never passed, also not while their pass-through is processed or with the checks off", () => {
    const current = photo("p1");
    expect(getShotCheckState(interior(current), [check("p1")], "on")).toEqual({ kind: "saved" });
    expect(getShotCheckState(interior(current, null, "detail"), [check("p1", { status: "checking" })], "on")).toEqual({
      kind: "saved",
    });
    expect(getShotCheckState(interior(current), [], "no_processor")).toEqual({ kind: "saved" });
    expect(getShotCheckState(interior(current), [check("p1", { status: "error", message: "Offline" })], "on")).toEqual({
      kind: "saved",
    });
    const processed = photo("p1", undefined, { processedStoragePath: "x.jpg", processedPreset: "autoexperten_standard" });
    expect(getShotCheckState(interior(processed), [], "on")).toEqual({ kind: "saved" });
  });

  it("show a refused file as a retake and nothing while being saved or missing", () => {
    expect(
      getShotCheckState(
        interior(photo("p1")),
        [check("p1", { status: "retake", message: "Zu groß.", errorCode: "image_too_large" })],
        "on",
      ),
    ).toEqual({ kind: "retake", reason: "Zu groß." });
    expect(getShotCheckState(interior(photo("p1"), pending("uploading")), [], "on")).toEqual({ kind: "none" });
    expect(getShotCheckState(interior(null), [], "on")).toEqual({ kind: "none" });
  });

  it("maps any state (pure)", () => {
    expect(passThroughState({ kind: "ok", hint: null }, true)).toEqual({ kind: "saved" });
    expect(passThroughState({ kind: "checking" }, true)).toEqual({ kind: "saved" });
    expect(passThroughState({ kind: "checking" }, false)).toEqual({ kind: "none" });
    expect(passThroughState({ kind: "retake", reason: "x" }, true)).toEqual({ kind: "retake", reason: "x" });
    // Nothing to report in the camera when such a check finishes.
    expect(checkResultMessage({ order: 13, title: "Tür & Sitzbedienung" }, { kind: "saved" })).toBeNull();
  });
});

describe("wording", () => {
  it("uses one label per state on every screen", () => {
    expect(shotCheckLabel({ kind: "none" })).toBeNull();
    expect(shotCheckLabel({ kind: "checking" })).toBe("Wird geprüft…");
    expect(shotCheckLabel({ kind: "ok", hint: null })).toBe("Geprüft – passt");
    expect(shotCheckLabel({ kind: "ok", hint: UNCERTAIN.message })).toBe("Geprüft – bitte Ergebnis ansehen");
    expect(shotCheckLabel({ kind: "saved" })).toBe("Gespeichert");
    expect(shotCheckLabel({ kind: "retake", reason: "x" })).toBe("Bitte neu aufnehmen");
    expect(shotCheckLabel({ kind: "error", reason: "x" })).toBe("Prüfung nicht möglich");
  });

  it("puts the processor's hint or reason after the label with a colon", () => {
    expect(shotCheckDetail({ kind: "ok", hint: null })).toBeNull();
    expect(shotCheckDetail({ kind: "saved" })).toBeNull();
    expect(shotCheckDescription({ kind: "none" })).toBeNull();
    expect(shotCheckDescription({ kind: "ok", hint: null })).toBe("Geprüft – passt");
    expect(shotCheckDescription({ kind: "ok", hint: UNCERTAIN.message })).toBe(
      `Geprüft – bitte Ergebnis ansehen: ${UNCERTAIN.message}`,
    );
    expect(shotCheckDescription({ kind: "retake", reason: TOO_SMALL })).toBe(`Bitte neu aufnehmen: ${TOO_SMALL}`);
    expect(shotCheckDescription({ kind: "error", reason: "Offline." })).toBe("Prüfung nicht möglich: Offline.");
    expect(shotCheckDescription({ kind: "saved" })).toBe("Gespeichert");
  });

  it("sums up the results to look at with the same wording", () => {
    expect(resultsToCheckMessage(1, "viewer")).toBe(
      "1 Foto: Geprüft – bitte Ergebnis ansehen (Hinweis in der Großansicht).",
    );
    expect(resultsToCheckMessage(3, "list")).toBe("3 Fotos: Geprüft – bitte Ergebnis ansehen (Hinweise in der Liste).");
  });

  it("counts the photos left to retake in correct German", () => {
    expect(retakesLeftMessage(1)).toBe("Noch 1 Foto neu aufnehmen");
    expect(retakesLeftMessage(2)).toBe("Noch 2 Fotos neu aufnehmen");
  });
});
