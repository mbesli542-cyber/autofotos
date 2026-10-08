import { describe, expect, it } from "vitest";
import { buildProcessingStatus } from "./processing-status";
import { parseProcessorHealth, parseProcessorJob } from "./processor-contract";
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
    metadata: { shotKind: "exterior_showroom", showroomSource: "master" },
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

  it("never reports a fallback-showroom result as complete", () => {
    const parsed = parseProcessorJob(
      job({ status: "complete", result: fileResult, metadata: { showroomSource: "fallback", showroomPlaceholder: true } }),
    );
    expect(parsed).toMatchObject({ status: "failed", result: null, error: SHOWROOM_MASTER_MISSING_MESSAGE });
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
    showroomSource: "master",
    showroomMasterError: null,
    presetError: null,
  };

  const statusFor = (health: ProcessorHealthReport | null, processor: "real" | "mock" = "real") =>
    buildProcessingStatus({ processor, health, accessCodeRequired: false, dataBackend: "demo" });

  it("is connected with the master showroom", () => {
    const status = statusFor(parseProcessorHealth(200, healthy));
    expect(status).toEqual({
      connected: true,
      processor: "real",
      showroomSource: "master",
      showroomError: null,
      accessCodeRequired: false,
      dataBackend: "demo",
    });
    expect(canStartProcessing(status)).toBe(true);
  });

  it("blocks processing while the showroom master is missing", () => {
    const status = statusFor(parseProcessorHealth(200, { ...healthy, showroomSource: "fallback" }));
    expect(status).toMatchObject({ connected: true, showroomSource: "fallback", showroomError: SHOWROOM_MASTER_MISSING_MESSAGE });
    expect(canStartProcessing(status)).toBe(false);
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
