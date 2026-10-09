import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import { ProcessingJobFailedError, processPhoto } from "@/lib/processing/photo-processing";
import { AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";
import { useProcessingRun } from "./use-processing-run";

vi.mock("@/lib/app-services", () => ({
  getAppServices: () => ({
    backend: { mode: "demo", data: {} },
    template: AUTOEXPERTEN_STANDARD_TEMPLATE,
  }),
}));

vi.mock("@/lib/processing/photo-processing", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/processing/photo-processing")>()),
  processPhoto: vi.fn(),
}));

vi.mock("@/lib/workflow/vehicle-workflow", () => ({ markProcessedIfDone: vi.fn(async () => false) }));

const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";

function photo(id: string, shotKey: string, shotOrder: number): VehiclePhotoWithUrls {
  return {
    id,
    vehicleId: "veh_1",
    shotKey,
    shotOrder,
    title: shotKey,
    originalStoragePath: `veh_1/${shotKey}/${id}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    thumbnailStoragePath: null,
    width: 4032,
    height: 3024,
    takenAt: "2026-10-08T10:00:00Z",
    createdAt: "2026-10-08T10:00:00Z",
    updatedAt: "2026-10-08T10:00:00Z",
    urls: { original: `blob:${id}`, thumbnail: `blob:${id}`, processed: null },
  };
}

describe("useProcessingRun", () => {
  beforeEach(() => {
    vi.mocked(processPhoto).mockReset();
    vi.mocked(markProcessedIfDone).mockClear();
  });

  it("keeps a rejected shot failed with its code and processes only its new photo after a retake", async () => {
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.id === "p_front") throw new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL);
    });
    const { result } = renderHook(() => useProcessingRun("veh_1"));

    await act(async () => {
      await result.current.start(
        [photo("p_fl", "front_left_45", 1), photo("p_front", "front", 2), photo("p_extra", "extra_01", 101)],
        "autoexperten_standard",
        null,
      );
    });
    expect(result.current.phase).toBe("done");
    expect(result.current.items.map(({ shotKey, status, errorCode, error }) => ({ shotKey, status, errorCode, error }))).toEqual([
      { shotKey: "front_left_45", status: "complete", errorCode: null, error: null },
      { shotKey: "front", status: "failed", errorCode: "vehicle_too_small", error: TOO_SMALL },
    ]);
    expect(markProcessedIfDone).toHaveBeenCalledTimes(1);

    vi.mocked(processPhoto).mockClear();
    vi.mocked(processPhoto).mockResolvedValue(undefined);
    const retaken = photo("p_front_2", "front", 2);
    await act(async () => {
      await result.current.retry(retaken, "autoexperten_standard", null);
    });
    expect(processPhoto).toHaveBeenCalledTimes(1);
    expect(vi.mocked(processPhoto).mock.calls[0]?.[0]).toBe(retaken);
    expect(result.current.phase).toBe("done");
    expect(result.current.items).toHaveLength(2);
    expect(result.current.items[1]).toMatchObject({
      photoId: "p_front_2",
      shotKey: "front",
      status: "complete",
      error: null,
      errorCode: null,
    });
    expect(result.current.items[0]).toMatchObject({ photoId: "p_fl", status: "complete" });
    expect(markProcessedIfDone).toHaveBeenCalledTimes(2);
  });
});
