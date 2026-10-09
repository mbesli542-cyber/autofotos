import { describe, expect, it } from "vitest";
import { buildProcessingStatus } from "./processing-status";
import { parseProcessorHealth, parseProcessorJob } from "./processor-contract";
import { QUALITY_GATE_ERROR_CODES, QUALITY_GATE_MESSAGES } from "./quality-gate";
import { SHOWROOM_MASTER_MISSING_MESSAGE, canStartProcessing, type ProcessorHealthReport } from "./types";

const JOB_ID = "fedcba9876543210fedcba9876543210";

function job(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    vehicleId: null,
    photoId: null,
    preset: "autoexperten_standard",
    status: "processing",
    progress: 0.4,
    createdAt: "2026-10-08T10:00:00Z",
    updatedAt: "2026-10-08T10:00:01Z",
    result: null,
    error: null,
    warnings: [{ code: "mask_small", message: "Das Fahrzeug ist sehr klein im Bild." }, { code: 3 }],
    metadata: { shotKind: "exterior_showroom", showroomSource: "plates", plateUsed: "front_left_45" },
    ...overrides,
  };
}

const fileResult = { kind: "file", resultUrl: `/jobs/${JOB_ID}/result`, width: 3200, height: 2400, bytes: 900000 };

describe("parseProcessorJob", () => {
  it("maps a running job and keeps only valid warnings", () => {
    expect(parseProcessorJob(job())).toEqual({
      jobId: JOB_ID,
      vehicleId: null,
      photoId: null,
      preset: "autoexperten_standard",
      status: "processing",
      progress: 0.4,
      createdAt: "2026-10-08T10:00:00Z",
      updatedAt: "2026-10-08T10:00:01Z",
      result: null,
      error: null,
      errorCode: null,
      warnings: [{ code: "mask_small", message: "Das Fahrzeug ist sehr klein im Bild." }],
    });
  });

  it("rejects unknown job ids and statuses", () => {
    expect(parseProcessorJob(job({ jobId: "mock_abc" }))).toBeNull();
    expect(parseProcessorJob(job({ status: "done" }))).toBeNull();
    expect(parseProcessorJob(null)).toBeNull();
  });

  it("keeps the processor's German error text for failed jobs", () => {
    const parsed = parseProcessorJob(
      job({ status: "failed", error: "Das Fahrzeug konnte im Foto nicht erkannt werden." }),
    );
    expect(parsed).toMatchObject({
      status: "failed",
      result: null,
      error: "Das Fahrzeug konnte im Foto nicht erkannt werden.",
    });
    expect(parseProcessorJob(job({ status: "failed", error: null }))?.error).toBe("Bearbeitung fehlgeschlagen.");
  });

  it("passes the errorCode of failed jobs on (quality gate)", () => {
    const parsed = parseProcessorJob(
      job({
        status: "failed",
        error: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
        metadata: { errorCode: "vehicle_too_small", qualityGate: { vehicleWidthRatio: 0.3 } },
      }),
    );
    expect(parsed).toMatchObject({
      status: "failed",
      result: null,
      error: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
      errorCode: "vehicle_too_small",
    });
    // Other failures keep their code too; running/complete jobs carry none.
    expect(parseProcessorJob(job({ status: "failed", metadata: { errorCode: "segmentation" } }))?.errorCode).toBe(
      "segmentation",
    );
    expect(parseProcessorJob(job({ metadata: { errorCode: "vehicle_too_small" } }))?.errorCode).toBeNull();
  });

  it("uses the German quality-gate message when the processor sends none", () => {
    for (const code of QUALITY_GATE_ERROR_CODES) {
      const parsed = parseProcessorJob(job({ status: "failed", error: null, metadata: { errorCode: code } }));
      expect(parsed).toMatchObject({ errorCode: code, error: QUALITY_GATE_MESSAGES[code] });
    }
    expect(QUALITY_GATE_MESSAGES.vehicle_too_small).toBe("Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.");
  });

  it("drops malformed error codes", () => {
    for (const errorCode of ["Vehicle Too Small", "x".repeat(41), "../etc", 7, null]) {
      expect(parseProcessorJob(job({ status: "failed", metadata: { errorCode } }))?.errorCode).toBeNull();
    }
  });

  it("never reports a fallback-showroom result as complete", () => {
    const parsed = parseProcessorJob(
      job({ status: "complete", result: fileResult, metadata: { showroomSource: "fallback", showroomPlaceholder: true } }),
    );
    expect(parsed).toMatchObject({
      status: "failed",
      result: null,
      error: SHOWROOM_MASTER_MISSING_MESSAGE,
      errorCode: "showroom",
    });
    for (const metadata of [{ showroomPlaceholder: true }, { showroomSource: "missing" }]) {
      expect(parseProcessorJob(job({ status: "complete", result: fileResult, metadata }))).toMatchObject({
        status: "failed",
        error: SHOWROOM_MASTER_MISSING_MESSAGE,
      });
    }
  });

  it("accepts results composited onto the showroom plates", () => {
    const parsed = parseProcessorJob(
      job({
        status: "complete",
        result: fileResult,
        metadata: { shotKind: "exterior_showroom", showroomSource: "plates", showroomPlaceholder: false, plateUsed: "front_right_45" },
      }),
    );
    expect(parsed).toMatchObject({ status: "complete", progress: 1, error: null, errorCode: null });
  });

  it("accepts interior passthrough results (no showroom involved)", () => {
    const parsed = parseProcessorJob(
      job({ status: "complete", result: fileResult, metadata: { shotKind: "interior_passthrough", showroomPlaceholder: false } }),
    );
    expect(parsed).toMatchObject({ status: "complete", progress: 1, result: { kind: "file", width: 3200 } });
  });

  it("treats a complete job without a usable result as failed", () => {
    expect(parseProcessorJob(job({ status: "complete", result: { kind: "mock_preview" } }))).toMatchObject({
      status: "failed",
      result: null,
    });
  });
});

describe("parseProcessorHealth + buildProcessingStatus", () => {
  const healthy = {
    status: "ok",
    version: "2",
    modelLoaded: true,
    modelError: false,
    showroomSource: "plates",
    showroomMasterError: null,
    presetError: null,
  };

  const statusFor = (health: ProcessorHealthReport | null, processor: "real" | "mock" = "real") =>
    buildProcessingStatus({ processor, health, accessCodeRequired: false, dataBackend: "demo" });

  it("is connected with the complete plate set", () => {
    const status = statusFor(parseProcessorHealth(200, healthy));
    expect(status).toEqual({
      connected: true,
      processor: "real",
      showroomSource: "plates",
      showroomError: null,
      accessCodeRequired: false,
      dataBackend: "demo",
    });
    expect(canStartProcessing(status)).toBe(true);
  });

  it("blocks processing while the plate set is missing or unusable", () => {
    const status = statusFor(
      parseProcessorHealth(200, { ...healthy, showroomSource: "missing", showroomMasterError: "plates.json fehlt" }),
    );
    expect(status).toMatchObject({ connected: true, showroomSource: "missing", showroomError: SHOWROOM_MASTER_MISSING_MESSAGE });
    expect(canStartProcessing(status)).toBe(false);

    const broken = statusFor(parseProcessorHealth(200, { ...healthy, showroomMasterError: "rear.jpg unlesbar" }));
    expect(broken.showroomError).toBe(SHOWROOM_MASTER_MISSING_MESSAGE);
    expect(canStartProcessing(broken)).toBe(false);
  });

  it("refuses the retired single master photo, the fallback and unknown sources", () => {
    for (const health of [
      { ...healthy, showroomSource: "master" },
      { ...healthy, showroomSource: "fallback" },
      { ...healthy, showroomSource: "plates", showroomPlaceholder: true },
      { ...healthy, showroomSource: "studio" },
      { ...healthy, showroomSource: null },
    ]) {
      const status = statusFor(parseProcessorHealth(200, health));
      expect(status).toMatchObject({ connected: true, showroomError: SHOWROOM_MASTER_MISSING_MESSAGE });
      expect(canStartProcessing(status)).toBe(false);
    }
  });

  it("reports a broken preset in German", () => {
    const status = statusFor(
      parseProcessorHealth(200, { ...healthy, showroomSource: null, presetError: "background.image missing" }),
    );
    expect(status.showroomError).toBe("Der AutoExperten Showroom ist auf dem Server nicht richtig eingerichtet.");
    expect(canStartProcessing(status)).toBe(false);
  });

  it("is not connected without processor, with a rejected key, a model error or no answer", () => {
    expect(statusFor(parseProcessorHealth(200, healthy), "mock").connected).toBe(false);
    expect(statusFor(parseProcessorHealth(200, { status: "ok", version: "2" })).connected).toBe(false);
    expect(statusFor(parseProcessorHealth(503, { ...healthy, status: "error", modelError: true })).connected).toBe(false);
    expect(statusFor(null).connected).toBe(false);
    expect(canStartProcessing(null)).toBe(false);
  });
});
