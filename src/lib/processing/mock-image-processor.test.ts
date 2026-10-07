import { describe, expect, it } from "vitest";
import { MockImageProcessor } from "./mock-image-processor";
import { parseProcessPhotoRequest } from "./types";

const REQUEST = {
  vehicleId: "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301",
  photoId: "bbbbbbbb-0000-0000-0000-000000000001",
  preset: "autoexperten_standard" as const,
};

describe("MockImageProcessor", () => {
  it("moves a job through queued → processing → complete over time", async () => {
    let now = 1_000_000;
    const processor = new MockImageProcessor({ queuedMs: 500, processingMs: 2000 }, () => now);
    const job = await processor.submit(REQUEST);
    expect(job).toMatchObject({ status: "queued", progress: 0, result: null, preset: "autoexperten_standard" });

    now += 1500;
    const processing = await processor.getJob(job.jobId);
    expect(processing?.status).toBe("processing");
    expect(processing?.progress).toBeCloseTo(0.5);

    now += 1500;
    const done = await processor.getJob(job.jobId);
    expect(done).toMatchObject({
      status: "complete",
      progress: 1,
      result: { kind: "mock_preview" },
      vehicleId: REQUEST.vehicleId,
      photoId: REQUEST.photoId,
    });
  });

  it("is stateless: a second instance can read the job", async () => {
    const created = await new MockImageProcessor().submit(REQUEST);
    const read = await new MockImageProcessor().getJob(created.jobId);
    expect(read?.photoId).toBe(REQUEST.photoId);
  });

  it("returns null for unknown or tampered job ids", async () => {
    const processor = new MockImageProcessor();
    expect(await processor.getJob("unknown")).toBeNull();
    expect(await processor.getJob("mock_bm90LWpzb24")).toBeNull();
  });
});

describe("parseProcessPhotoRequest", () => {
  it("accepts a valid request", () => {
    expect(parseProcessPhotoRequest(REQUEST)).toEqual({ ok: true, value: REQUEST });
  });

  it("rejects unknown presets and malformed ids with German messages", () => {
    expect(parseProcessPhotoRequest({ ...REQUEST, preset: "ai_magic" })).toEqual({
      ok: false,
      message: "Unbekannter Bearbeitungsstil.",
    });
    expect(parseProcessPhotoRequest({ ...REQUEST, photoId: "../etc" }).ok).toBe(false);
    expect(parseProcessPhotoRequest(null).ok).toBe(false);
  });
});
