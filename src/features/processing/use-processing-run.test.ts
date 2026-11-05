import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "@/lib/auth/auth-store";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import { readJobJournal, recordJob } from "@/lib/processing/job-journal";
import { ProcessingJobFailedError, processPhoto, type PhotoProcessingResult } from "@/lib/processing/photo-processing";
import { AccessCodeRequiredError } from "@/lib/processing/processing-client";
import { endProcessingSession } from "@/lib/processing/processing-runs";
import { readStoredRun, storeRun, type StoredRunItem } from "@/lib/processing/stored-runs";
import { AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import { markProcessedIfDone } from "@/lib/workflow/vehicle-workflow";
import { useProcessingRun, type ResumeResult, type RunResult } from "./use-processing-run";

/** Auth store stand-in: the signed-in user can change during a test. */
const session = vi.hoisted(() => {
  let state: AuthState = { status: "authenticated", user: { id: "u_anna", email: "anna@autoexperten-rn.de" } };
  const listeners = new Set<() => void>();
  return {
    authStore: {
      getSnapshot: () => state,
      subscribe: (listener: () => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
    set(next: AuthState) {
      state = next;
      for (const listener of listeners) listener();
    },
  };
});

/** The vehicle's photos as saved – by default unreadable, so a run uses the photos it was given. */
const saved = vi.hoisted(() => ({
  listPhotos: vi.fn(async (): Promise<unknown[]> => {
    throw new Error("offline");
  }),
}));

vi.mock("@/lib/app-services", () => ({
  getAppServices: () => ({
    backend: {
      mode: "demo",
      data: { listPhotos: saved.listPhotos, resolvePhotoUrls: async (photos: unknown[]) => photos },
    },
    template: AUTOEXPERTEN_STANDARD_TEMPLATE,
    authStore: session.authStore,
  }),
}));

vi.mock("@/lib/processing/photo-processing", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/processing/photo-processing")>()),
  processPhoto: vi.fn(),
}));

vi.mock("@/lib/workflow/vehicle-workflow", () => ({ markProcessedIfDone: vi.fn(async () => false) }));

const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";
/** A saved result without hints. */
const DONE: PhotoProcessingResult = { warnings: [] };
const MIRRORED = {
  code: "plate_mirrored",
  message:
    "Das Foto zeigt das Fahrzeug von der anderen Seite – es wurde der Showroom-Hintergrund „Vorne rechts (45°)“ verwendet. Bitte die Aufnahmeposition prüfen.",
};

/** Holds `processPhoto` calls until opened. */
function gate() {
  let open: () => void = () => {};
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
}

function photo(id: string, shotKey: string, shotOrder: number, vehicleId = "veh_1"): VehiclePhotoWithUrls {
  return {
    id,
    vehicleId,
    shotKey,
    shotOrder,
    title: shotKey,
    originalStoragePath: `veh_1/${shotKey}/${id}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
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
    window.localStorage.clear();
    vi.mocked(processPhoto).mockReset();
    vi.mocked(markProcessedIfDone).mockClear();
    saved.listPhotos.mockClear();
  });

  it("keeps a rejected shot failed with its code and processes only its new photo after a retake", async () => {
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.id === "p_front") throw new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL);
      return DONE;
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
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    const retaken = photo("p_front_2", "front", 2);
    await act(async () => {
      await result.current.retry([retaken], "autoexperten_standard", null);
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

  it("refuses styles the processor does not implement", async () => {
    const { result } = renderHook(() => useProcessingRun("veh_styles"));
    for (const preset of ["autoexperten_dark", "original_plus"] as const) {
      let outcome: RunResult | undefined;
      await act(async () => {
        outcome = await result.current.start([photo("p_s1", "front_left_45", 1, "veh_styles")], preset, null);
      });
      expect(outcome).toEqual({ outcome: "preset_unavailable", message: "Dieser Bearbeitungsstil ist noch nicht verfügbar." });
      await act(async () => {
        outcome = await result.current.retry([photo("p_s1", "front_left_45", 1, "veh_styles")], preset, null);
      });
      expect(outcome?.outcome).toBe("preset_unavailable");
    }
    expect(processPhoto).not.toHaveBeenCalled();
    expect(result.current.phase).toBe("idle");
  });

  it("keeps the run when the screen is re-created and refuses to start it twice", async () => {
    let finishJobs: () => void = () => {};
    const jobsDone = new Promise<PhotoProcessingResult>((resolve) => {
      finishJobs = () => resolve(DONE);
    });
    vi.mocked(processPhoto).mockImplementation(() => jobsDone);
    const photos = [photo("p_e1", "front_left_45", 1, "veh_evicted"), photo("p_e2", "front", 2, "veh_evicted")];

    const first = renderHook(() => useProcessingRun("veh_evicted"));
    let running: Promise<RunResult> | undefined;
    act(() => {
      running = first.result.current.start(photos, "autoexperten_standard", null);
    });
    expect(first.result.current.phase).toBe("running");
    first.unmount(); // the route was evicted from the router cache

    const second = renderHook(() => useProcessingRun("veh_evicted"));
    expect(second.result.current.phase).toBe("running");
    expect(second.result.current.items.map((item) => item.photoId)).toEqual(["p_e1", "p_e2"]);
    let again: RunResult | undefined;
    await act(async () => {
      again = await second.result.current.start(photos, "autoexperten_standard", null);
    });
    expect(again).toEqual({ outcome: "already_running" });
    // A shot that is still on its way is not submitted again either.
    await act(async () => {
      again = await second.result.current.retry([photos[0]!], "autoexperten_standard", null);
    });
    expect(again).toEqual({ outcome: "already_running" });

    await act(async () => {
      finishJobs();
      await running;
    });
    expect(second.result.current.phase).toBe("done");
    expect(second.result.current.items.every((item) => item.status === "complete")).toBe(true);
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_e1", "p_e2"]);
  });

  it("processes a photo again after a temporary failure", async () => {
    vi.mocked(processPhoto)
      .mockRejectedValueOnce(new AppError("network"))
      .mockResolvedValueOnce(DONE);
    const target = photo("p_t1", "rear", 7, "veh_temporary");
    const { result } = renderHook(() => useProcessingRun("veh_temporary"));

    await act(async () => {
      await result.current.start([target], "autoexperten_standard", null);
    });
    expect(result.current.items[0]).toMatchObject({
      status: "failed",
      errorCode: null,
      error: "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.",
    });

    await act(async () => {
      await result.current.retry([target], result.current.preset ?? "autoexperten_standard", null);
    });
    expect(result.current.items[0]).toMatchObject({ photoId: "p_t1", status: "complete", error: null });
    expect(processPhoto).toHaveBeenCalledTimes(2);
  });

  it("processes several new photos at most three at a time", async () => {
    const shots = ["front_left_45", "front", "front_right_45", "left_side", "right_side", "rear_left_45"];
    const { result } = renderHook(() => useProcessingRun("veh_batch"));
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    await act(async () => {
      await result.current.start(
        shots.map((shotKey, index) => photo(`p_old_${index}`, shotKey, index + 1, "veh_batch")),
        "autoexperten_standard",
        null,
      );
    });

    // "6 neue Fotos bereit – jetzt bearbeiten"
    const jobs = gate();
    let inFlight = 0;
    let maxInFlight = 0;
    vi.mocked(processPhoto).mockReset();
    vi.mocked(processPhoto).mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await jobs.opened;
      inFlight -= 1;
      return DONE;
    });
    const retakes = shots.map((shotKey, index) => photo(`p_new_${index}`, shotKey, index + 1, "veh_batch"));
    let done: Promise<RunResult> | undefined;
    await act(async () => {
      done = result.current.retry(retakes, "autoexperten_standard", null);
      await vi.waitFor(() => expect(inFlight).toBe(3));
    });
    await act(async () => {
      jobs.open();
      await done;
    });
    expect(maxInFlight).toBe(3);
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(retakes.map((target) => target.id));
    expect(result.current.phase).toBe("done");
    expect(result.current.items.map((item) => item.photoId)).toEqual(retakes.map((target) => target.id));
  });

  it("stops the run when the user signs out and leaves nothing of it for the next person", async () => {
    const jobs = gate();
    const signals: (AbortSignal | undefined)[] = [];
    vi.mocked(processPhoto).mockImplementation(async (_target, _preset, deps) => {
      signals.push(deps.signal);
      await jobs.opened;
      return DONE;
    });
    const photos = ["front_left_45", "front", "front_right_45", "left_side", "rear"].map((shotKey, index) =>
      photo(`p_so_${index}`, shotKey, index + 1, "veh_signout"),
    );
    const { result } = renderHook(() => useProcessingRun("veh_signout"));
    let running: Promise<RunResult> | undefined;
    await act(async () => {
      running = result.current.start(photos, "autoexperten_standard", null);
      await vi.waitFor(() => expect(signals).toHaveLength(3));
    });

    act(() => session.set({ status: "unauthenticated" })); // "Abmelden"
    expect(result.current.phase).toBe("idle");
    expect(result.current.items).toEqual([]);
    // Uploads and polls still on their way are aborted …
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
    let outcome: RunResult | undefined;
    await act(async () => {
      jobs.open();
      outcome = await running;
    });
    // … and the photos still waiting are never sent.
    expect(outcome).toEqual({ outcome: "cancelled" });
    expect(processPhoto).toHaveBeenCalledTimes(3);
    expect(markProcessedIfDone).not.toHaveBeenCalled();

    act(() => session.set({ status: "authenticated", user: { id: "u_ben", email: "ben@autoexperten-rn.de" } }));
    expect(result.current.phase).toBe("idle");
  });

  it("aborts what is still on its way when going back to the style selection", async () => {
    const signals: (AbortSignal | undefined)[] = [];
    const job = gate();
    vi.mocked(processPhoto).mockImplementation(async (_target, _preset, deps) => {
      signals.push(deps.signal);
      await job.opened;
      // Like processPhoto: an aborted processing saves nothing.
      if (deps.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return DONE;
    });
    const target = photo("p_r1", "rear", 7, "veh_reset");
    const { result } = renderHook(() => useProcessingRun("veh_reset"));
    let running: Promise<RunResult> | undefined;
    await act(async () => {
      running = result.current.start([target], "autoexperten_standard", null);
      await vi.waitFor(() => expect(signals).toHaveLength(1));
    });
    act(() => result.current.reset());
    expect(result.current.phase).toBe("idle");
    expect(signals[0]?.aborted).toBe(true);
    await act(async () => {
      job.open();
      expect(await running).toEqual({ outcome: "cancelled" });
    });
    expect(markProcessedIfDone).not.toHaveBeenCalled();

    // The same for a photo processed again.
    vi.mocked(processPhoto).mockRejectedValueOnce(new AppError("network"));
    await act(async () => {
      await result.current.start([target], "autoexperten_standard", null);
    });
    const again = gate();
    vi.mocked(processPhoto).mockImplementationOnce(async (_target, _preset, deps) => {
      signals.push(deps.signal);
      await again.opened;
      if (deps.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return DONE;
    });
    let retried: Promise<RunResult> | undefined;
    await act(async () => {
      retried = result.current.retry([target], "autoexperten_standard", null);
      await vi.waitFor(() => expect(signals).toHaveLength(2));
    });
    act(() => result.current.reset());
    expect(signals.at(-1)?.aborted).toBe(true);
    await act(async () => {
      again.open();
      expect(await retried).toEqual({ outcome: "cancelled" });
    });
  });

  it("keeps the processor's hints with the saved result and clears them when the shot is processed again", async () => {
    vi.mocked(processPhoto).mockResolvedValueOnce({ warnings: [MIRRORED] });
    const target = photo("p_w1", "front_left_45", 1, "veh_warnings");
    const { result } = renderHook(() => useProcessingRun("veh_warnings"));
    await act(async () => {
      await result.current.start([target], "autoexperten_standard", null);
    });
    expect(result.current.items[0]).toMatchObject({ status: "complete", warnings: [MIRRORED] });

    // "Foto neu aufnehmen": the result of the new photo has no hints.
    vi.mocked(processPhoto).mockResolvedValueOnce(DONE);
    const retaken = photo("p_w2", "front_left_45", 1, "veh_warnings");
    await act(async () => {
      await result.current.retry([retaken], "autoexperten_standard", null);
    });
    expect(result.current.items[0]).toMatchObject({ photoId: "p_w2", status: "complete", warnings: [] });
  });

  it("processes the photo the vehicle has now, not an older one still on screen", async () => {
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    const fl = photo("p_c_fl", "front_left_45", 1, "veh_current");
    const onScreen = [fl, photo("p_c_front_old", "front", 2, "veh_current")];
    // The retake of "Vorne" was saved a moment ago – the screen's reload is still on its way.
    saved.listPhotos.mockResolvedValueOnce([fl, photo("p_c_front_new", "front", 2, "veh_current")]);
    const { result } = renderHook(() => useProcessingRun("veh_current"));
    await act(async () => {
      await result.current.start(onScreen, "autoexperten_standard", null);
    });
    expect(saved.listPhotos).toHaveBeenCalledWith("veh_current");
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_c_fl", "p_c_front_new"]);
    expect(result.current.items.map((item) => item.photoId)).toEqual(["p_c_fl", "p_c_front_new"]);
  });

  it("processes the newest photo of a shot again and skips a shot whose photo is already processed", async () => {
    // "Vorne" failed for a temporary reason, "Vorne links" is done.
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.shotKey === "front") throw new AppError("network");
      return DONE;
    });
    const fl = photo("p_r_fl", "front_left_45", 1, "veh_retry_current");
    const oldFront = photo("p_r_front_old", "front", 2, "veh_retry_current");
    const { result } = renderHook(() => useProcessingRun("veh_retry_current"));
    await act(async () => {
      await result.current.start([fl, oldFront], "autoexperten_standard", null);
    });
    expect(result.current.items.map((item) => item.status)).toEqual(["complete", "failed"]);

    // Both again – meanwhile a retake of "Vorne" was saved.
    vi.mocked(processPhoto).mockReset();
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    saved.listPhotos.mockResolvedValueOnce([fl, photo("p_r_front_new", "front", 2, "veh_retry_current")]);
    await act(async () => {
      await result.current.retry([fl, oldFront], "autoexperten_standard", null);
    });
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_r_front_new"]);
    expect(result.current.items).toEqual([
      expect.objectContaining({ photoId: "p_r_fl", status: "complete" }),
      expect.objectContaining({ photoId: "p_r_front_new", status: "complete", error: null }),
    ]);
    expect(result.current.phase).toBe("done");
  });

  it("submits nothing when the run is reset while the current photos are read", async () => {
    let answer: (photos: unknown[]) => void = () => {};
    saved.listPhotos.mockImplementationOnce(
      () =>
        new Promise<unknown[]>((resolve) => {
          answer = resolve;
        }),
    );
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    const target = photo("p_rr", "rear", 7, "veh_reset_read");
    const { result } = renderHook(() => useProcessingRun("veh_reset_read"));
    let running: Promise<RunResult> | undefined;
    act(() => {
      running = result.current.start([target], "autoexperten_standard", null);
    });
    expect(result.current.phase).toBe("running");
    act(() => result.current.reset());
    await act(async () => {
      answer([target]);
      expect(await running).toEqual({ outcome: "cancelled" });
    });
    expect(processPhoto).not.toHaveBeenCalled();
    expect(result.current.phase).toBe("idle");
  });

  it("processes a retake saved while its shot still waits in the run – not the photo it replaced", async () => {
    const vehicleId = "veh_retake_waiting";
    const photos = ["front_left_45", "front", "front_right_45", "rear"].map((shotKey, index) =>
      photo(`p_w_${shotKey}`, shotKey, index + 1, vehicleId),
    );
    let savedNow: VehiclePhotoWithUrls[] = photos;
    saved.listPhotos.mockImplementation(async () => savedNow);
    try {
      const jobs = gate();
      vi.mocked(processPhoto).mockImplementation(async () => {
        await jobs.opened;
        return DONE;
      });
      const { result } = renderHook(() => useProcessingRun(vehicleId));
      let running: Promise<RunResult> | undefined;
      await act(async () => {
        running = result.current.start(photos, "autoexperten_standard", null);
        await vi.waitFor(() => expect(processPhoto).toHaveBeenCalledTimes(3));
      });
      expect(result.current.items.at(-1)).toMatchObject({ photoId: "p_w_rear", status: "waiting" });

      // "Hinten" is retaken (e.g. after leaving the screen) while it waits for a free place.
      savedNow = [...photos.slice(0, 3), photo("p_w_rear_retake", "rear", 4, vehicleId)];
      await act(async () => {
        jobs.open();
        await running;
      });
      expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual([
        "p_w_front_left_45",
        "p_w_front",
        "p_w_front_right_45",
        "p_w_rear_retake",
      ]);
      expect(result.current.items.map(({ shotKey, photoId, status }) => [shotKey, photoId, status])).toEqual([
        ["front_left_45", "p_w_front_left_45", "complete"],
        ["front", "p_w_front", "complete"],
        ["front_right_45", "p_w_front_right_45", "complete"],
        ["rear", "p_w_rear_retake", "complete"],
      ]);
      expect(result.current.phase).toBe("done");
    } finally {
      saved.listPhotos.mockImplementation(async () => {
        throw new Error("offline");
      });
    }
  });

  it("never processes a photo twice in one run – e.g. two photos of one shot on the list", async () => {
    const vehicleId = "veh_twice";
    const shown = photo("p_tw_rear_a", "rear", 7, vehicleId);
    const other = photo("p_tw_rear_b", "rear", 7, vehicleId);
    // Both still active (a damaged demo database): the shot's photo is the first one.
    saved.listPhotos.mockImplementation(async () => [shown, other]);
    try {
      vi.mocked(processPhoto).mockResolvedValue(DONE);
      const { result } = renderHook(() => useProcessingRun(vehicleId));
      await act(async () => {
        await result.current.start([shown, other], "autoexperten_standard", null);
      });
      expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_tw_rear_a"]);
      expect(result.current.items.map(({ photoId, status }) => [photoId, status])).toEqual([["p_tw_rear_a", "complete"]]);
      expect(result.current.phase).toBe("done");
    } finally {
      saved.listPhotos.mockImplementation(async () => {
        throw new Error("offline");
      });
    }
  });
  it("does not send a photo again whose result is known (checked after the capture)", async () => {
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.id === "p_k_rejected") throw new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL);
      return DONE;
    });
    const { result } = renderHook(() => useProcessingRun("veh_known"));
    const rejected = photo("p_k_rejected", "front_left_45", 1, "veh_known");
    // The automatic check rejected this photo before (same processing code path).
    await act(async () => {
      await result.current.start([rejected], "autoexperten_standard", null);
    });
    act(() => result.current.reset());
    vi.mocked(processPhoto).mockClear();

    const checked = {
      ...photo("p_k_done", "front", 2, "veh_known"),
      processedStoragePath: "vehicle-processed/veh_known/front/p_k_done.jpg",
      processedPreset: "autoexperten_standard" as const,
      processingWarnings: [MIRRORED],
    };
    const fresh = photo("p_k_new", "rear", 7, "veh_known");
    await act(async () => {
      await result.current.start([rejected, checked, fresh], "autoexperten_standard", null);
    });
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_k_new"]);
    expect(result.current.items.map(({ photoId, status, errorCode, warnings }) => ({ photoId, status, errorCode, warnings }))).toEqual([
      { photoId: "p_k_rejected", status: "failed", errorCode: "vehicle_too_small", warnings: [] },
      { photoId: "p_k_done", status: "complete", errorCode: null, warnings: [MIRRORED] },
      { photoId: "p_k_new", status: "complete", errorCode: null, warnings: [] },
    ]);
  });
  it("sends a rejected photo again on 'Erneut prüfen' and every photo when asked to reprocess", async () => {
    let gateCode: string | null = "vehicle_too_small";
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.id === "p_r_rejected" && gateCode) throw new ProcessingJobFailedError(gateCode, TOO_SMALL);
      return DONE;
    });
    const { result } = renderHook(() => useProcessingRun("veh_again"));
    const rejected = photo("p_r_rejected", "front_left_45", 1, "veh_again");
    const checked = {
      ...photo("p_r_done", "front", 2, "veh_again"),
      processedStoragePath: "vehicle-processed/veh_again/front/p_r_done.jpg",
      processedPreset: "autoexperten_standard" as const,
    };
    await act(async () => {
      await result.current.start([rejected, checked], "autoexperten_standard", null);
    });
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_r_rejected"]);
    expect(result.current.items[0]).toMatchObject({ status: "failed", errorCode: "vehicle_too_small" });

    // The quality gate was updated: "Erneut prüfen" sends the same photo again.
    gateCode = null;
    vi.mocked(processPhoto).mockClear();
    await act(async () => {
      await result.current.retry([rejected], "autoexperten_standard", null);
    });
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_r_rejected"]);
    expect(result.current.items[0]).toMatchObject({ photoId: "p_r_rejected", status: "complete", errorCode: null });

    // "Bereits geprüfte Fotos erneut bearbeiten" (e.g. after a showroom update): everything is sent.
    act(() => result.current.reset());
    vi.mocked(processPhoto).mockClear();
    await act(async () => {
      await result.current.start([rejected, checked], "autoexperten_standard", null, { reprocess: true });
    });
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id).sort()).toEqual(["p_r_done", "p_r_rejected"]);
    expect(result.current.items.every((item) => item.status === "complete")).toBe(true);
  });
});

describe("useProcessingRun after a reload", () => {
  const JOB = "0123456789abcdef0123456789abcdef";
  const ANNA: AuthState = { status: "authenticated", user: { id: "u_anna", email: "anna@autoexperten-rn.de" } };

  beforeEach(() => {
    session.set(ANNA);
    window.localStorage.clear();
    vi.mocked(processPhoto).mockReset();
    vi.mocked(processPhoto).mockResolvedValue(DONE);
    saved.listPhotos.mockReset();
    saved.listPhotos.mockImplementation(async () => {
      throw new Error("offline");
    });
  });

  function storedItem(target: VehiclePhotoWithUrls, status: StoredRunItem["status"], patch: Partial<StoredRunItem> = {}) {
    return {
      photoId: target.id,
      shotKey: target.shotKey,
      title: target.title,
      shotOrder: target.shotOrder,
      treatment: "showroom" as const,
      status,
      error: null,
      errorCode: null,
      warnings: [],
      ...patch,
    };
  }

  it("keeps a running run on the device and forgets it once it is done", async () => {
    const jobs = gate();
    vi.mocked(processPhoto).mockImplementation(async (target) => {
      if (target.id === "p_k2") await jobs.opened;
      return DONE;
    });
    const photos = [photo("p_k1", "front_left_45", 1, "veh_keep"), photo("p_k2", "front", 2, "veh_keep")];
    const { result } = renderHook(() => useProcessingRun("veh_keep"));
    let running: Promise<RunResult> | undefined;
    act(() => {
      running = result.current.start(photos, "autoexperten_standard", null);
    });
    await vi.waitFor(() => expect(readStoredRun("veh_keep")?.items.map((item) => item.status)).toEqual(["complete", "open"]));
    expect(readStoredRun("veh_keep")).toMatchObject({ userId: "u_anna", preset: "autoexperten_standard", reprocess: false });
    await act(async () => {
      jobs.open();
      await running;
    });
    expect(result.current.phase).toBe("done");
    expect(readStoredRun("veh_keep")).toBeNull();
  });

  it("continues the run: finished rows stay, the others are processed with the shot's current photo", async () => {
    const done = photo("p_c1", "front_left_45", 1, "veh_cont");
    const replaced = photo("p_c2_old", "front", 2, "veh_cont");
    const retake = photo("p_c2_new", "front", 2, "veh_cont");
    const open = photo("p_c3", "front_right_45", 3, "veh_cont");
    storeRun({
      userId: "u_anna",
      vehicleId: "veh_cont",
      preset: "autoexperten_standard",
      reprocess: false,
      startedAt: new Date().toISOString(),
      items: [storedItem(done, "complete"), storedItem(replaced, "open"), storedItem(open, "open")],
    });
    // The job of p_c3 was submitted before the reload.
    recordJob({
      userId: "u_anna",
      vehicleId: "veh_cont",
      photoId: "p_c3",
      jobId: JOB,
      preset: "autoexperten_standard",
      startedAt: new Date().toISOString(),
      shotKey: open.shotKey,
      shotOrder: open.shotOrder,
      title: open.title,
      takenAt: open.takenAt,
    });
    saved.listPhotos.mockImplementation(async () => [done, retake, open]);

    const { result } = renderHook(() => useProcessingRun("veh_cont"));
    expect(result.current.phase).toBe("running");
    await vi.waitFor(() => expect(result.current.phase).toBe("done"));
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id).sort()).toEqual(["p_c2_new", "p_c3"]);
    const resumed = vi.mocked(processPhoto).mock.calls.find(([target]) => target.id === "p_c3");
    expect(resumed?.[2].resumeJobId).toBe(JOB);
    expect(result.current.items.map(({ photoId, status }) => ({ photoId, status }))).toEqual([
      { photoId: "p_c1", status: "complete" },
      { photoId: "p_c2_new", status: "complete" },
      { photoId: "p_c3", status: "complete" },
    ]);
    expect(readStoredRun("veh_cont")).toBeNull();
  });

  it("sends again photos with an older result when the run re-processes everything", async () => {
    const processed = {
      ...photo("p_rp1", "front_left_45", 1, "veh_reproc"),
      processedStoragePath: "vehicle-processed/veh_reproc/front_left_45/p_rp1.jpg",
      processedPreset: "autoexperten_standard" as const,
    };
    storeRun({
      userId: "u_anna",
      vehicleId: "veh_reproc",
      preset: "autoexperten_standard",
      reprocess: true,
      startedAt: new Date().toISOString(),
      items: [storedItem(processed, "open")],
    });
    saved.listPhotos.mockImplementation(async () => [processed]);
    const { result } = renderHook(() => useProcessingRun("veh_reproc"));
    await vi.waitFor(() => expect(result.current.phase).toBe("done"));
    expect(vi.mocked(processPhoto).mock.calls.map(([target]) => target.id)).toEqual(["p_rp1"]);
  });

  it("does nothing without a stored run and forgets a run whose access code is refused – the screen is told", async () => {
    const none = renderHook(() => useProcessingRun("veh_none"));
    await act(async () => {});
    expect(none.result.current.phase).toBe("idle");

    window.localStorage.setItem("ae-photo.processing-access-code", "falsch");
    const target = photo("p_ad1", "front", 2, "veh_denied");
    storeRun({
      userId: "u_anna",
      vehicleId: "veh_denied",
      preset: "autoexperten_standard",
      reprocess: false,
      startedAt: new Date().toISOString(),
      items: [storedItem(target, "open")],
    });
    saved.listPhotos.mockImplementation(async () => [target]);
    vi.mocked(processPhoto).mockRejectedValue(new AccessCodeRequiredError(401, "unauthorized"));
    const resumed: ResumeResult[] = [];
    const { result } = renderHook(() => useProcessingRun("veh_denied", (outcome) => resumed.push(outcome)));
    await vi.waitFor(() => expect(resumed).toHaveLength(1));
    // The screen asks for the code again and retries with the run's own style.
    expect(resumed[0]).toMatchObject({ outcome: "access_denied", preset: "autoexperten_standard" });
    expect(resumed[0]?.message).toBeTruthy();
    expect(readStoredRun("veh_denied")).toBeNull();
    expect(result.current.phase).toBe("idle");
    expect(window.localStorage.getItem("ae-photo.processing-access-code")).toBeNull();
  });

  it("continues only the run of the signed-in user – another user's run is forgotten, nothing is sent", async () => {
    const target = photo("p_ben1", "front", 2, "veh_ben");
    storeRun({
      userId: "u_ben",
      vehicleId: "veh_ben",
      preset: "autoexperten_standard",
      reprocess: false,
      startedAt: new Date().toISOString(),
      items: [storedItem(target, "open")],
    });
    saved.listPhotos.mockImplementation(async () => [target]);
    const resumed: ResumeResult[] = [];
    const { result } = renderHook(() => useProcessingRun("veh_ben", (outcome) => resumed.push(outcome)));
    await act(async () => {});
    expect(result.current.phase).toBe("idle");
    expect(readStoredRun("veh_ben")).toBeNull();
    expect(processPhoto).not.toHaveBeenCalled();
    expect(resumed).toEqual([]);
  });

  it("forgets the stored runs and kept jobs on sign-out – also when nothing ran in this page load", async () => {
    const target = photo("p_so1", "front", 2, "veh_so");
    storeRun({
      userId: "u_anna",
      vehicleId: "veh_so",
      preset: "autoexperten_standard",
      reprocess: false,
      startedAt: new Date().toISOString(),
      items: [storedItem(target, "open")],
    });
    recordJob({
      userId: "u_anna",
      vehicleId: "veh_so",
      photoId: "p_so1",
      jobId: JOB,
      preset: "autoexperten_standard",
      startedAt: new Date().toISOString(),
      shotKey: target.shotKey,
      shotOrder: target.shotOrder,
      title: target.title,
      takenAt: target.takenAt,
    });
    // "Abmelden" on the "Mehr" page after a reload.
    endProcessingSession();
    expect(readStoredRun("veh_so")).toBeNull();
    expect(readJobJournal()).toEqual([]);

    // The next person opens "Fotos bearbeiten": the style selection, nothing continues.
    act(() => session.set({ status: "authenticated", user: { id: "u_ben", email: "ben@autoexperten-rn.de" } }));
    const { result } = renderHook(() => useProcessingRun("veh_so"));
    await act(async () => {});
    expect(result.current.phase).toBe("idle");
    expect(processPhoto).not.toHaveBeenCalled();
  });
});
