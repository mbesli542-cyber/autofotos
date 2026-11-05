import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VehiclePhoto } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import { ProcessingJobFailedError, processPhoto, type PhotoProcessingResult } from "./photo-processing";
import { storeAccessCode } from "./access-code-storage";
import { CHECK_PRESET, MAX_PARALLEL_JOBS, PhotoChecks, knownOutcomeOf, type PhotoCheck } from "./photo-checks";
import { AccessCodeRequiredError } from "./processing-client";
import { ACCESS_CODE_INVALID_CODE } from "./shot-check";
import type { ProcessingStatus } from "./types";

/** The vehicle's saved photos (current ones) and the services the checks use. */
const env = vi.hoisted(() => ({
  photos: [] as unknown[],
  uploaded: [] as ((item: unknown) => void)[],
}));

vi.mock("@/lib/app-services", () => ({
  getAppServices: () => ({
    backend: {
      mode: "demo",
      data: {
        listPhotos: async (vehicleId: string) =>
          (env.photos as { vehicleId: string }[]).filter((photo) => photo.vehicleId === vehicleId),
        resolvePhotoUrls: async (photos: unknown[]) =>
          (photos as { id: string }[]).map((photo) => ({
            ...photo,
            urls: { original: `blob:${photo.id}`, thumbnail: `blob:${photo.id}`, processed: null },
          })),
        getVehicle: async () => ({ status: "capturing" }),
      },
    },
    template: AUTOEXPERTEN_STANDARD_TEMPLATE,
    uploads: {
      onUploaded: (listener: (item: unknown) => void) => {
        env.uploaded.push(listener);
        return () => {};
      },
    },
    authStore: {
      getSnapshot: () => ({ status: "authenticated", user: { id: "u_anna", email: "anna@autoexperten-rn.de" } }),
      subscribe: () => () => {},
    },
  }),
}));

vi.mock("./photo-processing", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./photo-processing")>()),
  processPhoto: vi.fn(),
}));

vi.mock("@/lib/workflow/vehicle-workflow", () => ({ markProcessedIfDone: vi.fn(async () => false) }));

const CONNECTED: ProcessingStatus = {
  connected: true,
  processor: "real",
  showroomSource: "plates",
  showroomError: null,
  connectionError: null,
  accessCodeRequired: false,
  dataBackend: "demo",
};

let status: ProcessingStatus = CONNECTED;
const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";

function savedPhoto(id: string, shotKey: string, takenAt = "2026-11-03T10:00:00.000Z"): VehiclePhoto {
  const shot = AUTOEXPERTEN_STANDARD_TEMPLATE.shots.find((entry) => entry.key === shotKey);
  return {
    id,
    vehicleId: "veh_1",
    shotKey,
    shotOrder: shot?.order ?? 101,
    title: shot?.title ?? shotKey,
    originalStoragePath: `veh_1/${shotKey}/${id}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt,
    createdAt: takenAt,
    updatedAt: takenAt,
  };
}

function uploadItem(photo: VehiclePhoto): UploadItem {
  return {
    id: photo.id,
    vehicleId: photo.vehicleId,
    shotKey: photo.shotKey,
    shotOrder: photo.shotOrder,
    title: photo.title,
    takenAt: photo.takenAt,
    status: "uploading",
    thumbnailUrl: null,
    error: null,
    retryable: true,
  };
}

/** A capture saved by the upload queue: becomes the shot's photo, then the queue reports it. */
function save(photo: VehiclePhoto) {
  env.photos = [...(env.photos as VehiclePhoto[]).filter((entry) => entry.shotKey !== photo.shotKey), photo];
  for (const listener of env.uploaded) listener(uploadItem(photo));
}

function gate() {
  let open: () => void = () => {};
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function started(): PhotoChecks {
  const checks = new PhotoChecks();
  checks.start();
  return checks;
}

const statusOf = (checks: PhotoChecks, photoId: string) => checks.getSnapshot().checks.get(photoId)?.status;

beforeEach(() => {
  status = CONNECTED;
  env.photos = [];
  env.uploaded = [];
  window.localStorage.clear();
  vi.mocked(processPhoto).mockReset();
  vi.mocked(processPhoto).mockResolvedValue({ warnings: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (String(input).endsWith("/api/processing-status")) {
        return new Response(JSON.stringify(status), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new TypeError(`unexpected fetch ${String(input)}`);
    }),
  );
});

describe("PhotoChecks", () => {
  it("checks a saved capture with the standard style and reports the result", async () => {
    const checks = started();
    const settled: PhotoCheck[] = [];
    checks.onSettled((check) => settled.push(check));
    save(savedPhoto("p_front", "front"));
    await vi.waitFor(() => expect(statusOf(checks, "p_front")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(1);
    expect(vi.mocked(processPhoto).mock.calls[0]?.[0]).toMatchObject({ id: "p_front", urls: { original: "blob:p_front" } });
    expect(vi.mocked(processPhoto).mock.calls[0]?.[1]).toBe(CHECK_PRESET);
    expect(settled.map(({ photoId, status: state }) => ({ photoId, state }))).toEqual([{ photoId: "p_front", state: "ok" }]);
    expect(checks.getSnapshot().availability).toBe("on");
  });

  it("forgets a deleted vehicle: its running check is aborted, its stored rejection removed – others stay", async () => {
    vi.mocked(processPhoto).mockRejectedValueOnce(new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL));
    const checks = started();
    save({ ...savedPhoto("p_other_rear", "rear"), vehicleId: "veh_2" });
    await vi.waitFor(() => expect(statusOf(checks, "p_other_rear")).toBe("retake"));
    vi.mocked(processPhoto).mockRejectedValueOnce(new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL));
    save(savedPhoto("p_del_fl", "front_left_45"));
    await vi.waitFor(() => expect(statusOf(checks, "p_del_fl")).toBe("retake"));
    let signal: AbortSignal | undefined;
    vi.mocked(processPhoto).mockImplementationOnce(
      (_photo, _preset, options) =>
        new Promise<PhotoProcessingResult>((_resolve, reject) => {
          signal = options.signal;
          options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    save(savedPhoto("p_del_front", "front"));
    await vi.waitFor(() => expect(signal).toBeDefined());

    checks.stopVehicle("veh_1");
    expect(signal?.aborted).toBe(true);
    expect([...checks.getSnapshot().checks.keys()]).toEqual(["p_other_rear"]);
    expect([...checks.getSnapshot().captures.keys()]).toEqual(["veh_2/rear"]);
    await flush();
    expect(statusOf(checks, "p_del_front")).toBeUndefined();
    // Only the other vehicle's rejection comes back after a reload.
    expect([...started().getSnapshot().checks.keys()]).toEqual(["p_other_rear"]);

    checks.clear();
    expect(checks.getSnapshot().checks.size).toBe(0);
    expect(checks.getSnapshot().captures.size).toBe(0);
    expect(started().getSnapshot().checks.size).toBe(0);
  });

  it("keeps a rejection with the processor's reason – also after a reload of the page", async () => {
    vi.mocked(processPhoto).mockRejectedValue(new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL));
    const checks = started();
    save(savedPhoto("p_fl", "front_left_45"));
    await vi.waitFor(() => expect(statusOf(checks, "p_fl")).toBe("retake"));
    expect(checks.getSnapshot().checks.get("p_fl")).toMatchObject({ message: TOO_SMALL, errorCode: "vehicle_too_small" });

    const reloaded = started();
    expect(reloaded.getSnapshot().checks.get("p_fl")).toMatchObject({
      status: "retake",
      message: TOO_SMALL,
      errorCode: "vehicle_too_small",
      restored: true,
    });
    // Only shown: the quality gate may have changed since – "Fotos bearbeiten" sends it again.
    const photo = { ...savedPhoto("p_fl", "front_left_45"), urls: { original: "", thumbnail: "", processed: null } };
    expect(checks.knownOutcome(photo, CHECK_PRESET)).toMatchObject({ status: "failed" });
    expect(reloaded.knownOutcome(photo, CHECK_PRESET)).toBeNull();
  });

  it("sends nothing without a connected processor or without the stored access code – the capture waits", async () => {
    status = { ...CONNECTED, connected: false, showroomSource: null };
    const offline = started();
    save(savedPhoto("p_off_front", "front"));
    // Configured but not answering (PC asleep): "nicht erreichbar", never "nicht verbunden".
    await vi.waitFor(() => expect(offline.getSnapshot().availability).toBe("unreachable"));
    env.uploaded = [];

    status = { ...CONNECTED, connected: false, processor: "mock", showroomSource: null };
    const none = started();
    save(savedPhoto("p_none_left", "left_side"));
    await vi.waitFor(() => expect(none.getSnapshot().availability).toBe("no_processor"));
    env.uploaded = [];

    status = { ...CONNECTED, accessCodeRequired: true };
    const demo = started();
    save(savedPhoto("p_off_rear", "rear"));
    await vi.waitFor(() => expect(demo.getSnapshot().availability).toBe("access_code"));
    await flush();
    expect(processPhoto).not.toHaveBeenCalled();
    expect(statusOf(offline, "p_off_front")).toBe("waiting");
    expect(statusOf(none, "p_none_left")).toBe("waiting");
    expect(statusOf(demo, "p_off_rear")).toBe("waiting");
  });

  it("checks a capture taken while the checks were off as soon as they are on again", async () => {
    status = { ...CONNECTED, accessCodeRequired: true };
    const checks = started();
    save(savedPhoto("p_wait_rear", "rear"));
    await vi.waitFor(() => expect(checks.getSnapshot().availability).toBe("access_code"));
    expect(processPhoto).not.toHaveBeenCalled();
    storeAccessCode("test");
    await vi.waitFor(() => expect(statusOf(checks, "p_wait_rear")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(1);
    storeAccessCode(null);
  });

  it("knows the new capture as the shot's photo at once – before any request", () => {
    const checks = started();
    save(savedPhoto("p_sync_old", "front", "2026-11-03T10:00:00.000Z"));
    save(savedPhoto("p_sync_new", "front", "2026-11-03T10:01:00.000Z"));
    // No await: the replaced photo's check is gone and the new one waits in the same tick.
    expect(statusOf(checks, "p_sync_new")).toBe("waiting");
    expect(checks.getSnapshot().checks.has("p_sync_old")).toBe(false);
    expect(checks.getSnapshot().captures.get("veh_1/front")).toEqual({
      photoId: "p_sync_new",
      takenAt: "2026-11-03T10:01:00.000Z",
    });
  });

  it("lets a capture wait instead of reporting an error when the processor is gone", async () => {
    vi.mocked(processPhoto).mockImplementation(async () => {
      status = { ...CONNECTED, connected: false, showroomSource: null };
      throw new AppError("network");
    });
    const checks = started();
    const settled: string[] = [];
    checks.onSettled((check) => settled.push(check.photoId));
    save(savedPhoto("p_gone_front", "front"));
    await vi.waitFor(() => expect(checks.getSnapshot().availability).toBe("unreachable"));
    await vi.waitFor(() => expect(statusOf(checks, "p_gone_front")).toBe("waiting"));
    expect(settled).toEqual([]);

    // Back again: the waiting capture is checked.
    status = CONNECTED;
    vi.mocked(processPhoto).mockResolvedValue({ warnings: [] });
    await checks.refreshAvailability(true);
    await vi.waitFor(() => expect(statusOf(checks, "p_gone_front")).toBe("ok"));
    expect(settled).toEqual(["p_gone_front"]);
  });

  it("reports a wrong access code and checks the photo again once a code is entered", async () => {
    status = { ...CONNECTED, accessCodeRequired: true };
    storeAccessCode("WRONG");
    vi.mocked(processPhoto).mockRejectedValueOnce(new AccessCodeRequiredError(401, "unauthorized"));
    const checks = started();
    save(savedPhoto("p_code_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_code_rear")).toBe("error"));
    expect(checks.getSnapshot().checks.get("p_code_rear")?.errorCode).toBe(ACCESS_CODE_INVALID_CODE);
    await vi.waitFor(() => expect(checks.getSnapshot().availability).toBe("access_code"));

    storeAccessCode("test");
    await vi.waitFor(() => expect(statusOf(checks, "p_code_rear")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(2);
    storeAccessCode(null);
  });

  it("checks a photo again whose check failed (checkVehicle)", async () => {
    vi.mocked(processPhoto).mockRejectedValueOnce(new AppError("unknown"));
    const checks = started();
    save(savedPhoto("p_fail_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_fail_rear")).toBe("error"));
    await checks.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(checks, "p_fail_rear")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(2);
  });

  it("never runs more than MAX_PARALLEL_JOBS processings together with a run", async () => {
    const jobs = gate();
    let inFlight = 0;
    let maxInFlight = 0;
    vi.mocked(processPhoto).mockImplementation(async (): Promise<PhotoProcessingResult> => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await jobs.opened;
      inFlight -= 1;
      return { warnings: [] };
    });
    const checks = started();
    save(savedPhoto("p_par_front", "front"));
    save(savedPhoto("p_par_rear", "rear"));
    await vi.waitFor(() => expect(inFlight).toBe(2));
    // A processing run sends two more photos meanwhile.
    const runPhotos = ["front_left_45", "front_right_45"].map((shotKey) => ({
      ...savedPhoto(`p_run_${shotKey}`, shotKey),
      urls: { original: "", thumbnail: "", processed: null },
    }));
    const runs = runPhotos.map((photo) => checks.process(photo, CHECK_PRESET, { accessCode: null }));
    await flush();
    expect(inFlight).toBe(MAX_PARALLEL_JOBS);
    jobs.open();
    await Promise.all(runs);
    expect(maxInFlight).toBe(MAX_PARALLEL_JOBS);
  });

  it("never checks extra photos", async () => {
    const checks = started();
    save(savedPhoto("p_extra", "extra_01"));
    await flush();
    await flush();
    expect(processPhoto).not.toHaveBeenCalled();
    expect(checks.getSnapshot().checks.size).toBe(0);
  });

  it("checks at most two photos at a time", async () => {
    const jobs = gate();
    let inFlight = 0;
    let maxInFlight = 0;
    vi.mocked(processPhoto).mockImplementation(async (): Promise<PhotoProcessingResult> => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await jobs.opened;
      inFlight -= 1;
      return { warnings: [] };
    });
    const checks = started();
    const shots = AUTOEXPERTEN_STANDARD_TEMPLATE.shots.slice(0, 5);
    for (const shot of shots) save(savedPhoto(`p_${shot.key}`, shot.key));
    await vi.waitFor(() => expect(inFlight).toBe(2));
    expect(shots.filter((shot) => statusOf(checks, `p_${shot.key}`) === "waiting")).toHaveLength(3);
    jobs.open();
    await vi.waitFor(() => expect(shots.every((shot) => statusOf(checks, `p_${shot.key}`) === "ok")).toBe(true));
    expect(maxInFlight).toBe(2);
  });

  it("drops the check of a photo a retake replaced – its result is never reported", async () => {
    const first = gate();
    vi.mocked(processPhoto).mockImplementation(async (photo, _preset, deps) => {
      if (photo.id === "p_old") {
        await new Promise<void>((resolve, reject) => {
          void first.opened.then(resolve);
          deps.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
        throw new ProcessingJobFailedError("vehicle_too_small", TOO_SMALL);
      }
      return { warnings: [] };
    });
    const checks = started();
    const settled: string[] = [];
    checks.onSettled((check) => settled.push(check.photoId));
    save(savedPhoto("p_old", "front", "2026-11-03T10:00:00.000Z"));
    await vi.waitFor(() => expect(statusOf(checks, "p_old")).toBe("checking"));
    save(savedPhoto("p_new", "front", "2026-11-03T10:01:00.000Z"));
    await vi.waitFor(() => expect(statusOf(checks, "p_new")).toBe("ok"));
    first.open();
    await flush();
    expect(checks.getSnapshot().checks.has("p_old")).toBe(false);
    expect(settled).toEqual(["p_new"]);
    expect(vi.mocked(processPhoto).mock.calls[0]?.[2].signal?.aborted).toBe(true);
  });

  it("lets a processing run join a check that is still running instead of sending the photo twice", async () => {
    const job = gate();
    vi.mocked(processPhoto).mockImplementation(async () => {
      await job.opened;
      return { warnings: [] };
    });
    const checks = started();
    const photo = savedPhoto("p_front", "front");
    save(photo);
    await vi.waitFor(() => expect(statusOf(checks, "p_front")).toBe("checking"));
    const joined = checks.process(
      { ...photo, urls: { original: "blob:p_front", thumbnail: "blob:p_front", processed: null } },
      CHECK_PRESET,
      { accessCode: null },
    );
    job.open();
    await expect(joined).resolves.toEqual({ warnings: [] });
    expect(processPhoto).toHaveBeenCalledTimes(1);
  });
});

describe("knownOutcomeOf", () => {
  const photo = { ...savedPhoto("p_front", "front"), urls: { original: "", thumbnail: "", processed: null } };
  const rejected: PhotoCheck = {
    photoId: "p_front",
    vehicleId: "veh_1",
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    takenAt: photo.takenAt,
    preset: CHECK_PRESET,
    status: "retake",
    message: TOO_SMALL,
    errorCode: "vehicle_too_small",
    warnings: [],
  };

  it("knows a saved result of the same style and a rejection of exactly this photo", () => {
    const warnings = [{ code: "vehicle_small", message: "Klein" }];
    expect(
      knownOutcomeOf(
        { ...photo, processedStoragePath: "x.jpg", processedPreset: CHECK_PRESET, processingWarnings: warnings },
        CHECK_PRESET,
        undefined,
      ),
    ).toEqual({ status: "complete", warnings });
    expect(knownOutcomeOf(photo, CHECK_PRESET, rejected)).toEqual({
      status: "failed",
      error: TOO_SMALL,
      errorCode: "vehicle_too_small",
    });
  });

  it("knows a result saved by the photo's check in this visit (the list on screen may be older)", () => {
    const warnings = [{ code: "vehicle_part_uncertain", message: "Bitte prüfen" }];
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, status: "ok", errorCode: null, warnings })).toEqual({
      status: "complete",
      warnings,
    });
    expect(knownOutcomeOf(photo, "autoexperten_dark", { ...rejected, status: "ok", errorCode: null })).toBeNull();
  });

  it("ignores a rejection when asked to (Erneut prüfen) or when it was read back from the device", () => {
    expect(knownOutcomeOf(photo, CHECK_PRESET, rejected, { ignoreFailures: true })).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, restored: true })).toBeNull();
  });

  it("processes everything else", () => {
    expect(knownOutcomeOf({ ...photo, processedStoragePath: "x.jpg", processedPreset: "autoexperten_dark" }, CHECK_PRESET, undefined)).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, photoId: "p_other" })).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, status: "error", errorCode: null })).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, status: "checking" })).toBeNull();
  });
});
