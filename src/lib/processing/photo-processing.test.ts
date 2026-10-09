import { describe, expect, it, vi } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import { ProcessingJobFailedError, processPhoto, type PhotoProcessingDeps } from "./photo-processing";
import type { ProcessingJob } from "./types";

const JOB_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const PHOTO: VehiclePhotoWithUrls = {
  id: "photo_1",
  vehicleId: "veh_1",
  shotKey: "front_left_45",
  shotOrder: 1,
  title: "Vorne links (45°)",
  originalStoragePath: "vehicle-originals/veh_1/front_left_45/photo_1.jpg",
  processedStoragePath: null,
  processedPreset: null,
  thumbnailStoragePath: null,
  width: 4032,
  height: 3024,
  takenAt: "2026-10-08T10:00:00Z",
  createdAt: "2026-10-08T10:00:00Z",
  updatedAt: "2026-10-08T10:00:00Z",
  urls: { original: "blob:original", thumbnail: "blob:thumb", processed: null },
};

function job(overrides: Partial<ProcessingJob>): ProcessingJob {
  return {
    jobId: JOB_ID,
    vehicleId: null,
    photoId: null,
    preset: "autoexperten_standard",
    status: "complete",
    progress: 1,
    createdAt: "",
    updatedAt: "",
    result: null,
    error: null,
    errorCode: null,
    warnings: [],
    ...overrides,
  };
}

function setup(mode: "demo" | "supabase", finalJob: ProcessingJob) {
  const original = new Blob(["original"], { type: "image/jpeg" });
  const upload = new Blob(["downscaled"], { type: "image/jpeg" });
  const result = new Blob(["showroom"], { type: "image/jpeg" });
  const api = {
    submitPhotoProcessing: vi.fn(async () => ({ jobId: JOB_ID, status: "queued" as const })),
    submitUploadProcessing: vi.fn(async () => ({ jobId: JOB_ID, status: "queued" as const })),
    waitForProcessingJob: vi.fn(async () => finalJob),
    downloadProcessingResult: vi.fn(async () => result),
  };
  const data = { saveProcessedPhoto: vi.fn(async () => PHOTO), recordProcessedPhoto: vi.fn(async () => undefined) };
  const deps: PhotoProcessingDeps = {
    mode,
    data,
    accessCode: "code-1",
    api,
    loadOriginal: vi.fn(async () => original),
    prepareUpload: vi.fn(async () => upload),
  };
  return { api, data, deps, original, upload, result };
}

describe("processPhoto – demo upload adapter", () => {
  it("uploads a prepared copy with the shot key and stores the downloaded result", async () => {
    const ctx = setup("demo", job({ result: { kind: "file", width: 3200, height: 2400, bytes: 8 } }));
    await processPhoto(PHOTO, "autoexperten_standard", ctx.deps);

    expect(ctx.deps.loadOriginal).toHaveBeenCalledWith("blob:original", undefined);
    expect(ctx.deps.prepareUpload).toHaveBeenCalledWith(ctx.original);
    expect(ctx.api.submitUploadProcessing).toHaveBeenCalledWith(
      { file: ctx.upload, preset: "autoexperten_standard", shotKey: "front_left_45" },
      { accessCode: "code-1", signal: undefined },
    );
    expect(ctx.api.downloadProcessingResult).toHaveBeenCalledWith(JOB_ID, { accessCode: "code-1", signal: undefined });
    expect(ctx.data.saveProcessedPhoto).toHaveBeenCalledWith({ photo: PHOTO, preset: "autoexperten_standard", file: ctx.result });
    expect(ctx.api.submitPhotoProcessing).not.toHaveBeenCalled();
  });

  it("keeps the processor's German error and saves nothing", async () => {
    const ctx = setup("demo", job({ status: "failed", error: "Das Fahrzeug konnte im Foto nicht erkannt werden." }));
    await expect(processPhoto(PHOTO, "autoexperten_standard", ctx.deps)).rejects.toThrow(
      "Das Fahrzeug konnte im Foto nicht erkannt werden.",
    );
    expect(ctx.api.downloadProcessingResult).not.toHaveBeenCalled();
    expect(ctx.data.saveProcessedPhoto).not.toHaveBeenCalled();
  });

  it("passes the quality-gate code of a rejected photo on and saves nothing", async () => {
    const ctx = setup(
      "demo",
      job({
        status: "failed",
        error: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
        errorCode: "vehicle_too_small",
      }),
    );
    const error = await processPhoto(PHOTO, "autoexperten_standard", ctx.deps).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ProcessingJobFailedError);
    expect(error).toMatchObject({
      errorCode: "vehicle_too_small",
      message: "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.",
    });
    expect(ctx.api.downloadProcessingResult).not.toHaveBeenCalled();
    expect(ctx.data.saveProcessedPhoto).not.toHaveBeenCalled();
  });

  it("refuses jobs without a file result", async () => {
    const ctx = setup("demo", job({ result: { kind: "stored", processedStoragePath: "x.jpg" } }));
    await expect(processPhoto(PHOTO, "autoexperten_standard", ctx.deps)).rejects.toThrow(
      "Die Bildbearbeitung hat kein Ergebnis geliefert.",
    );
    expect(ctx.data.saveProcessedPhoto).not.toHaveBeenCalled();
    expect(ctx.data.recordProcessedPhoto).not.toHaveBeenCalled();
  });
});

describe("processPhoto – Supabase contract adapter", () => {
  it("submits ids only and links the stored result", async () => {
    const ctx = setup(
      "supabase",
      job({ result: { kind: "stored", processedStoragePath: "veh_1/autoexperten_standard/front_left_45/photo_1.jpg" } }),
    );
    await processPhoto(PHOTO, "autoexperten_standard", ctx.deps);

    expect(ctx.api.submitPhotoProcessing).toHaveBeenCalledWith(
      { vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" },
      { accessCode: "code-1", signal: undefined },
    );
    expect(ctx.data.recordProcessedPhoto).toHaveBeenCalledWith({
      photoId: "photo_1",
      preset: "autoexperten_standard",
      processedStoragePath: "veh_1/autoexperten_standard/front_left_45/photo_1.jpg",
    });
    expect(ctx.deps.loadOriginal).not.toHaveBeenCalled();
    expect(ctx.api.submitUploadProcessing).not.toHaveBeenCalled();
    expect(ctx.data.saveProcessedPhoto).not.toHaveBeenCalled();
  });

  it("refuses file results (nothing stored on the server)", async () => {
    const ctx = setup("supabase", job({ result: { kind: "file", width: 1, height: 1, bytes: 1 } }));
    await expect(processPhoto(PHOTO, "autoexperten_standard", ctx.deps)).rejects.toThrow();
    expect(ctx.data.recordProcessedPhoto).not.toHaveBeenCalled();
  });
});
