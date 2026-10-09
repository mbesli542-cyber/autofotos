import { describe, expect, it } from "vitest";
import {
  QUALITY_GATE_ERROR_CODES,
  QUALITY_GATE_MESSAGES,
  getRetakeStatus,
  isQualityGateErrorCode,
  needsRetake,
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
    for (const code of [...QUALITY_GATE_ERROR_CODES, "segmentation", "decode"]) {
      expect(needsRetake(code)).toBe(true);
    }
    for (const code of ["configuration", "showroom", "service", "storage", "unknown", "not_found", null, undefined]) {
      expect(needsRetake(code)).toBe(false);
    }
  });
});

describe("getRetakeStatus", () => {
  const failed = { photoId: "p_old", shotKey: "front", status: "failed", errorCode: "vehicle_too_small" };
  const oldPhoto = { id: "p_old", shotKey: "front" };
  const newPhoto = { id: "p_new", shotKey: "front" };
  const other = { id: "p_x", shotKey: "rear" };

  it("offers the retake while the rejected photo is current", () => {
    expect(getRetakeStatus(failed, [other, oldPhoto], [])).toEqual({ kind: "retake" });
    // Photo deleted in the meantime – still a retake.
    expect(getRetakeStatus(failed, [other], [])).toEqual({ kind: "retake" });
  });

  it("waits for a new capture that is still being saved", () => {
    expect(
      getRetakeStatus(failed, [oldPhoto], [
        { id: "u1", shotKey: "rear", status: "queued" },
        { id: "u2", shotKey: "front", status: "uploading" },
      ]),
    ).toEqual({ kind: "saving", uploadId: "u2", failed: false });
    expect(getRetakeStatus(failed, [oldPhoto], [{ id: "u3", shotKey: "front", status: "failed" }])).toEqual({
      kind: "saving",
      uploadId: "u3",
      failed: true,
    });
  });

  it("offers processing once the new photo replaced the rejected one", () => {
    expect(getRetakeStatus(failed, [other, newPhoto], [])).toEqual({ kind: "ready", photo: newPhoto });
  });

  it("is null for successful items and failures a retake does not fix", () => {
    expect(getRetakeStatus({ ...failed, status: "complete" }, [newPhoto], [])).toBeNull();
    expect(getRetakeStatus({ ...failed, status: "processing" }, [oldPhoto], [])).toBeNull();
    expect(getRetakeStatus({ ...failed, errorCode: "configuration" }, [oldPhoto], [])).toBeNull();
    expect(getRetakeStatus({ ...failed, errorCode: null }, [oldPhoto], [])).toBeNull();
  });
});
