import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppError } from "@/lib/errors";
import { submitPhotoProcessing } from "./processing-client";
import { PROCESSING_BUSY_CODE } from "./types";

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const busy = () =>
  jsonResponse({ error: { code: PROCESSING_BUSY_CODE, message: "Die Bildbearbeitung ist gerade ausgelastet." } }, 503);

const REQUEST = { vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" } as const;

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("submitPhotoProcessing", () => {
  it("retries while the processor is busy and then succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(busy())
      .mockResolvedValueOnce(busy())
      .mockResolvedValueOnce(jsonResponse({ jobId: "job_1", status: "queued" }, 202));

    const pending = submitPhotoProcessing(REQUEST);
    await vi.runAllTimersAsync();

    await expect(pending).resolves.toEqual({ jobId: "job_1", status: "queued" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("gives up after the retries with the German busy message", async () => {
    fetchMock.mockImplementation(async () => busy());

    const pending = submitPhotoProcessing(REQUEST);
    const outcome = expect(pending).rejects.toThrow("Die Bildbearbeitung ist gerade ausgelastet.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("does not retry other errors", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "processing_unavailable", message: "nicht erreichbar" } }, 502),
    );

    await expect(submitPhotoProcessing(REQUEST)).rejects.toBeInstanceOf(AppError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
