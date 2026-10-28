import { describe, expect, it } from "vitest";
import {
  IMAGE_TOO_LARGE_MESSAGE,
  QUALITY_GATE_ERROR_CODES,
  QUALITY_GATE_MESSAGES,
  getRetakeStatus,
  isQualityGateErrorCode,
  needsRetake,
  retakeFallbackMessage,
} from "./quality-gate";

describe("quality-gate codes", () => {
  it("pins the processor's German messages", () => {
    expect(QUALITY_GATE_MESSAGES).toEqual({
      source_resolution_too_low:
        "Die Auflösung des Fotos ist zu gering. Bitte Foto in voller Kamera-Auflösung neu aufnehmen.",
      vehicle_too_small: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
      vehicle_cropped:
        "Das Fahrzeug ist im Foto angeschnitten. Bitte das ganze Fahrzeug mit etwas Abstand neu fotografieren.",
      mask_low_confidence:
        "Das Fahrzeug konnte nicht sicher freigestellt werden. Bitte Foto vor ruhigerem Hintergrund neu aufnehmen.",
      ground_contact_uncertain:
        "Die Bodenkontakte der Reifen sind nicht erkennbar. Bitte Foto neu aufnehmen – alle Räder müssen sichtbar sein.",
      perspective_mismatch:
        "Die Perspektive passt nicht zum Showroom. Bitte aus Brusthöhe und mit etwas Abstand neu fotografieren.",
    });
    expect(Object.keys(QUALITY_GATE_MESSAGES)).toEqual([...QUALITY_GATE_ERROR_CODES]);
  });

  it("recognises only the gate codes", () => {
    expect(QUALITY_GATE_ERROR_CODES.every(isQualityGateErrorCode)).toBe(true);
    for (const value of ["segmentation", "configuration", "", null, undefined, 1]) {
      expect(isQualityGateErrorCode(value)).toBe(false);
    }
  });

  it("asks for a retake for photo problems only", () => {
    for (const code of [...QUALITY_GATE_ERROR_CODES, "segmentation", "decode", "image_too_large"]) {
      expect(needsRetake(code)).toBe(true);
    }
    for (const code of ["configuration", "showroom", "service", "storage", "unknown", "not_found", null, undefined]) {
      expect(needsRetake(code)).toBe(false);
    }
  });

  it("explains photos above 80 megapixels instead of calling them unreadable", () => {
    expect(IMAGE_TOO_LARGE_MESSAGE).toBe(
      "Das Foto hat eine zu hohe Auflösung (über 80 Megapixel). Bitte in normaler Kamera-Auflösung neu aufnehmen.",
    );
    expect(retakeFallbackMessage("image_too_large")).toBe(IMAGE_TOO_LARGE_MESSAGE);
    expect(retakeFallbackMessage("vehicle_too_small")).toBe(QUALITY_GATE_MESSAGES.vehicle_too_small);
    for (const code of ["decode", "segmentation", "configuration", null, undefined]) {
      expect(retakeFallbackMessage(code)).toBeNull();
    }
  });
});

describe("getRetakeStatus", () => {
  /** Saved photos (taken and saved at 10:00) and captures taken later (10:05) – the usual case. */
  const SAVED_AT = "2026-10-28T10:00:00.000Z";
  const LATER = "2026-10-28T10:05:00.000Z";
  const saved = (id: string, shotKey: string) => ({ id, shotKey, takenAt: SAVED_AT, createdAt: SAVED_AT });
  const failed = { photoId: "p_old", shotKey: "front", status: "failed", errorCode: "vehicle_too_small" };
  const oldPhoto = saved("p_old", "front");
  const newPhoto = { ...saved("p_new", "front"), takenAt: "2026-10-28T10:01:00.000Z", createdAt: "2026-10-28T10:01:00.000Z" };
  const other = saved("p_x", "rear");
  const capture = (id: string, shotKey: string, status: string, extra: { retryable?: boolean; error?: string; takenAt?: string } = {}) => ({
    id,
    shotKey,
    status,
    takenAt: LATER,
    ...extra,
  });

  it("offers the retake while the rejected photo is current", () => {
    expect(getRetakeStatus(failed, [other, oldPhoto], [])).toEqual({ kind: "retake" });
    // Photo deleted in the meantime – still a retake.
    expect(getRetakeStatus(failed, [other], [])).toEqual({ kind: "retake" });
  });

  it("waits for a new capture that is still being saved", () => {
    expect(
      getRetakeStatus(failed, [oldPhoto], [
        capture("u1", "rear", "queued"),
        capture("u2", "front", "uploading"),
      ]),
    ).toEqual({ kind: "saving", uploadId: "u2", failed: false, error: null });
    expect(
      getRetakeStatus(failed, [oldPhoto], [
        capture("u3", "front", "failed", { retryable: true, error: "Verbindung fehlgeschlagen." }),
      ]),
    ).toEqual({ kind: "saving", uploadId: "u3", failed: true, error: "Verbindung fehlgeschlagen." });
  });

  it("ignores a capture the server refused for good – it never becomes the shot's photo", () => {
    const refused = capture("u5", "front", "failed", {
      retryable: false,
      error: "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).",
    });
    const processed = { photoId: "p_old", shotKey: "front", status: "complete", errorCode: null };
    expect(getRetakeStatus(processed, [oldPhoto], [refused])).toBeNull();
    expect(getRetakeStatus(failed, [oldPhoto], [refused])).toEqual({ kind: "retake" });
    // An older capture of the shot that can still be saved still counts.
    expect(
      getRetakeStatus(processed, [oldPhoto], [capture("u4", "front", "queued", { retryable: true }), refused]),
    ).toEqual({ kind: "saving", uploadId: "u4", failed: false, error: null });
  });

  it("offers processing once the new photo replaced the rejected one", () => {
    expect(getRetakeStatus(failed, [other, newPhoto], [])).toEqual({ kind: "ready", photo: newPhoto });
  });

  it("is null for current results, running items and failures a retake does not fix", () => {
    expect(getRetakeStatus({ ...failed, status: "complete", errorCode: null }, [oldPhoto], [])).toBeNull();
    expect(getRetakeStatus({ ...failed, status: "processing" }, [newPhoto], [capture("u1", "front", "queued")])).toBeNull();
    expect(getRetakeStatus({ ...failed, errorCode: "configuration" }, [oldPhoto], [])).toBeNull();
    expect(getRetakeStatus({ ...failed, errorCode: null }, [oldPhoto], [])).toBeNull();
  });

  it("offers a new photo that replaced an already processed one (retake saved during the run)", () => {
    const processed = { photoId: "p_old", shotKey: "front", status: "complete", errorCode: null };
    expect(getRetakeStatus(processed, [other, newPhoto], [])).toEqual({ kind: "ready", photo: newPhoto });
    expect(getRetakeStatus(processed, [oldPhoto], [capture("u4", "front", "uploading")])).toEqual({
      kind: "saving",
      uploadId: "u4",
      failed: false,
      error: null,
    });
    // Also after a failure a retake does not fix (e.g. connection lost).
    expect(getRetakeStatus({ ...failed, errorCode: null }, [newPhoto], [])).toEqual({ kind: "ready", photo: newPhoto });
  });

  it("offers a retake for a saved result whose photo shows the other side of the car – not for hints to check", () => {
    const mirrored = { code: "plate_mirrored", message: "Das Foto zeigt das Fahrzeug von der anderen Seite …" };
    const removed = { code: "background_object_removed", message: "… wurde entfernt – bitte das Ergebnis prüfen." };
    const processed = { photoId: "p_old", shotKey: "front", status: "complete", errorCode: null };
    expect(getRetakeStatus({ ...processed, warnings: [mirrored] }, [oldPhoto], [])).toEqual({ kind: "retake" });
    expect(getRetakeStatus({ ...processed, warnings: [removed] }, [oldPhoto], [])).toBeNull();
    // Once the new photo is there, it is offered for processing.
    expect(getRetakeStatus({ ...processed, warnings: [mirrored] }, [newPhoto], [])).toEqual({ kind: "ready", photo: newPhoto });
  });

  it("ignores a capture older than the shot's saved photo – it is stored archived (like the photo slots)", () => {
    const processed = { photoId: "p_old", shotKey: "front", status: "complete", errorCode: null };
    const older = capture("u6", "front", "failed", { retryable: true, error: "Verbindung fehlgeschlagen.", takenAt: "2026-10-28T09:50:00.000Z" });
    expect(getRetakeStatus(processed, [oldPhoto], [older])).toBeNull();
    expect(getRetakeStatus(failed, [oldPhoto], [older])).toEqual({ kind: "retake" });
    // A retake saved meanwhile is offered – not the older capture that is still waiting.
    expect(getRetakeStatus(processed, [newPhoto], [{ ...older, takenAt: "2026-10-28T10:00:30.000Z" }])).toEqual({
      kind: "ready",
      photo: newPhoto,
    });
    // Equal times: the capture wins, as in the database.
    expect(getRetakeStatus(processed, [oldPhoto], [{ ...older, takenAt: SAVED_AT }])).toMatchObject({ kind: "saving", uploadId: "u6" });
  });

  it("counts a saved photo dated ahead (device clock) as taken at most 5 minutes after it was saved", () => {
    const processed = { photoId: "p_old", shotKey: "front", status: "complete", errorCode: null };
    const ahead = { ...oldPhoto, takenAt: "2026-10-28T12:00:00.000Z" };
    expect(getRetakeStatus(processed, [ahead], [capture("u7", "front", "queued", { takenAt: "2026-10-28T10:04:00.000Z" })])).toBeNull();
    expect(getRetakeStatus(processed, [ahead], [capture("u7", "front", "queued", { takenAt: "2026-10-28T10:06:00.000Z" })])).toEqual({
      kind: "saving",
      uploadId: "u7",
      failed: false,
      error: null,
    });
  });
});
