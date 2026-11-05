import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VehiclePhoto } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import { ProcessingJobFailedError, processPhoto, type PhotoProcessingResult } from "./photo-processing";
import { storeAccessCode } from "./access-code-storage";
import { readJobJournal, recordJob } from "./job-journal";
import {
  AVAILABILITY_POLL_MS,
  CHECK_PRESET,
  MAX_PARALLEL_JOBS,
  PhotoChecks,
  checkOnOpen,
  isKeptOutcome,
  knownOutcomeOf,
  shouldPollAvailability,
  type PhotoCheck,
} from "./photo-checks";
import { AccessCodeRequiredError } from "./processing-client";
import { ACCESS_CODE_INVALID_CODE } from "./shot-check";
import type { ProcessingStatus } from "./types";

/** The vehicle's saved photos (current ones) and the services the checks use. */
const env = vi.hoisted(() => ({
  photos: [] as unknown[],
  uploaded: [] as ((item: unknown) => void)[],
  /** The next this many reads of the photo list fail (weak connection). */
  unreadable: 0,
  /** The signed-in user. */
  user: "u_anna",
}));

vi.mock("@/lib/app-services", () => ({
  getAppServices: () => ({
    backend: {
      mode: "demo",
      data: {
        listPhotos: async (vehicleId: string) => {
          if (env.unreadable > 0) {
            env.unreadable -= 1;
            throw new TypeError("Failed to fetch");
          }
          return (env.photos as { vehicleId: string }[]).filter((photo) => photo.vehicleId === vehicleId);
        },
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
      getSnapshot: () => ({ status: "authenticated", user: { id: env.user, email: `${env.user}@autoexperten-rn.de` } }),
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
  env.unreadable = 0;
  env.user = "u_anna";
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
    // Jobs kept on the device for both vehicles (submitted before a reload).
    const kept = (vehicleId: string, photoId: string, jobId: string) => ({
      userId: "u_anna",
      vehicleId,
      photoId,
      jobId,
      preset: CHECK_PRESET,
      startedAt: new Date().toISOString(),
      shotKey: "rear",
      shotOrder: 7,
      title: "Hinten",
      takenAt: "2026-11-03T10:00:00.000Z",
    });
    recordJob(kept("veh_1", "p_del_rear", "0123456789abcdef0123456789abcde1"));
    recordJob(kept("veh_2", "p_other_rear", "0123456789abcdef0123456789abcde2"));

    checks.stopVehicle("veh_1");
    expect(signal?.aborted).toBe(true);
    // Nothing of the deleted vehicle is polled or resumed after a reload.
    expect(readJobJournal().map((entry) => entry.vehicleId)).toEqual(["veh_2"]);
    expect([...checks.getSnapshot().checks.keys()]).toEqual(["p_other_rear"]);
    expect([...checks.getSnapshot().captures.keys()]).toEqual(["veh_2/rear"]);
    await flush();
    expect(statusOf(checks, "p_del_front")).toBeUndefined();
    // Only the other vehicle's rejection comes back after a reload.
    expect([...started().getSnapshot().checks.keys()]).toEqual(["p_other_rear"]);

    checks.clear();
    expect(checks.getSnapshot().checks.size).toBe(0);
    expect(checks.getSnapshot().captures.size).toBe(0);
    expect(readJobJournal()).toEqual([]);
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

  it("marks a rejection of a photo that had a result ('Erneut prüfen') – also after a reload", async () => {
    vi.mocked(processPhoto).mockRejectedValueOnce(new ProcessingJobFailedError("vehicle_cropped", "Angeschnitten."));
    const checks = started();
    const processed = {
      ...savedPhoto("p_again", "front"),
      processedStoragePath: "veh_1/autoexperten_standard/front/p_again.jpg",
      processedPreset: CHECK_PRESET,
      updatedAt: "2026-11-03T10:02:00.000Z",
      urls: { original: "blob:p_again", thumbnail: "", processed: "blob:p_again_processed" },
    };
    await expect(checks.process(processed, CHECK_PRESET, { accessCode: null })).rejects.toBeInstanceOf(ProcessingJobFailedError);
    expect(checks.getSnapshot().checks.get("p_again")).toMatchObject({ status: "retake", afterResult: processed.updatedAt });
    expect(started().getSnapshot().checks.get("p_again")).toMatchObject({
      status: "retake",
      restored: true,
      afterResult: processed.updatedAt,
    });
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

  it("never repeats checks in a loop when the processor refuses a code that a cached status did not ask for", async () => {
    storeAccessCode("ALT");
    const checks = started();
    await checks.refreshAvailability(true); // cached: no code needed
    vi.mocked(processPhoto).mockRejectedValue(new AccessCodeRequiredError(401, "unauthorized"));
    save(savedPhoto("p_loop_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_loop_rear")).toBe("error"));
    await flush();
    await flush();
    expect(checks.getSnapshot().availability).toBe("access_code");
    expect(processPhoto).toHaveBeenCalledTimes(1);

    // A new code: checked once more.
    vi.mocked(processPhoto).mockResolvedValue({ warnings: [] });
    storeAccessCode("NEU");
    await vi.waitFor(() => expect(statusOf(checks, "p_loop_rear")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(2);
    storeAccessCode(null);
  });

  it("checks the photos without a check when a page opens – a failed check is not sent again on every visit", async () => {
    vi.mocked(processPhoto).mockImplementation(async (photo) => {
      if (photo.id === "p_fail_rear") throw new ProcessingJobFailedError("processing_failed", "Bearbeitung fehlgeschlagen.");
      return { warnings: [] };
    });
    const checks = started();
    save(savedPhoto("p_fail_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_fail_rear")).toBe("error"));
    // Taken while this tab did not follow the uploads (e.g. another device).
    env.photos = [...(env.photos as VehiclePhoto[]), savedPhoto("p_new_front", "front")];
    for (let visit = 0; visit < 4; visit += 1) await checks.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(checks, "p_new_front")).toBe("ok"));
    expect(vi.mocked(processPhoto).mock.calls.map(([photo]) => photo.id)).toEqual(["p_fail_rear", "p_new_front"]);
    expect(statusOf(checks, "p_fail_rear")).toBe("error");
  });

  it("keeps a failed check after a reload – it is not sent again on every page load, only by 'Fotos bearbeiten'", async () => {
    vi.mocked(processPhoto).mockRejectedValue(new ProcessingJobFailedError("processing_failed", "Bearbeitung fehlgeschlagen."));
    const checks = started();
    save(savedPhoto("p_failed_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_failed_rear")).toBe("error"));

    for (let load = 0; load < 3; load += 1) {
      const reloaded = started();
      expect(reloaded.getSnapshot().checks.get("p_failed_rear")).toMatchObject({
        status: "error",
        message: "Bearbeitung fehlgeschlagen.",
        errorCode: "processing_failed",
        restored: true,
      });
      await reloaded.checkVehicle("veh_1");
      await flush();
    }
    expect(processPhoto).toHaveBeenCalledTimes(1);
    // Only shown: "Fotos bearbeiten" processes it.
    const photo = { ...savedPhoto("p_failed_rear", "rear"), urls: { original: "", thumbnail: "", processed: null } };
    expect(started().knownOutcome(photo, CHECK_PRESET)).toBeNull();
  });

  it("keeps rejections and failures by the photo, but not a refused access code", () => {
    expect(isKeptOutcome({ status: "retake", errorCode: "vehicle_cropped" })).toBe(true);
    expect(isKeptOutcome({ status: "retake", errorCode: null })).toBe(false); // result saved with the photo
    expect(isKeptOutcome({ status: "error", errorCode: "processing_failed" })).toBe(true);
    expect(isKeptOutcome({ status: "error", errorCode: null })).toBe(true);
    expect(isKeptOutcome({ status: "error", errorCode: ACCESS_CODE_INVALID_CODE })).toBe(false);
    expect(isKeptOutcome({ status: "ok", errorCode: null })).toBe(false);
  });

  it("repeats a check that could not run when the checks come on again", async () => {
    vi.mocked(processPhoto).mockRejectedValueOnce(new AppError("unknown"));
    const checks = started();
    save(savedPhoto("p_fail_rear", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_fail_rear")).toBe("error"));
    await checks.checkVehicle("veh_1");
    expect(processPhoto).toHaveBeenCalledTimes(1);

    // The processor was gone for a while and answers again.
    status = { ...CONNECTED, connected: false, showroomSource: null };
    await checks.refreshAvailability(true);
    status = CONNECTED;
    await checks.refreshAvailability(true);
    await vi.waitFor(() => expect(statusOf(checks, "p_fail_rear")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(2);
  });

  it("does not send a photo again by itself whose rejection is kept with it (e.g. from another device)", async () => {
    vi.mocked(processPhoto).mockResolvedValue({ warnings: [] });
    const checks = started();
    env.photos = [
      { ...savedPhoto("p_kept_rear", "rear"), processingWarnings: [{ code: "rejected:vehicle_too_small", message: TOO_SMALL }] },
      savedPhoto("p_kept_front", "front"),
    ];
    await checks.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(checks, "p_kept_front")).toBe("ok"));
    expect(checks.getSnapshot().checks.has("p_kept_rear")).toBe(false);
    expect(vi.mocked(processPhoto).mock.calls.map(([photo]) => photo.id)).toEqual(["p_kept_front"]);
  });

  it("does not send a waiting photo that was rejected or processed meanwhile (e.g. on another device)", async () => {
    vi.mocked(processPhoto).mockRejectedValueOnce(new AppError("unknown", { userMessage: "Bearbeitung fehlgeschlagen." }));
    const checks = started();
    save(savedPhoto("p_meanwhile", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_meanwhile")).toBe("error"));
    // Another device's check rejected it; the checks come on again and repeat what failed here.
    env.photos = [
      { ...savedPhoto("p_meanwhile", "rear"), processingWarnings: [{ code: "rejected:vehicle_too_small", message: TOO_SMALL }] },
    ];
    status = { ...CONNECTED, connected: false, showroomSource: null };
    await checks.refreshAvailability(true);
    status = CONNECTED;
    await checks.refreshAvailability(true);
    await vi.waitFor(() => expect(checks.getSnapshot().checks.has("p_meanwhile")).toBe(false));
    expect(processPhoto).toHaveBeenCalledTimes(1);
  });

  it("polls the kept job of a check that could not run when a page opens – the photo is not sent again", async () => {
    const JOB = "0123456789abcdef0123456789abcdef";
    vi.mocked(processPhoto).mockImplementationOnce(async (_photo, _preset, deps) => {
      deps.onSubmitted?.(JOB);
      throw new AppError("network"); // the answer did not arrive, the processor still answers its status
    });
    const checks = started();
    save(savedPhoto("p_lost", "rear"));
    await vi.waitFor(() => expect(statusOf(checks, "p_lost")).toBe("error"));
    expect(readJobJournal().map((entry) => entry.jobId)).toEqual([JOB]);

    await checks.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(checks, "p_lost")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(2);
    expect(vi.mocked(processPhoto).mock.calls[1]?.[2].resumeJobId).toBe(JOB);
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

  it("treats a rejection that came after the photo's unchanged result like any rejection ('Erneut prüfen')", () => {
    const processed = { ...photo, processedStoragePath: "x.jpg", processedPreset: CHECK_PRESET, updatedAt: "2026-11-03T10:02:00.000Z" };
    const after = { ...rejected, afterResult: processed.updatedAt };
    // This visit's verdict: not sent again by a new run – the shot is red on every screen of this device.
    expect(knownOutcomeOf(processed, CHECK_PRESET, after)).toEqual({ status: "failed", error: TOO_SMALL, errorCode: "vehicle_too_small" });
    // Read back from the device, or asked again: processed again (the gate may have changed).
    expect(knownOutcomeOf(processed, CHECK_PRESET, { ...after, restored: true })).toBeNull();
    expect(knownOutcomeOf(processed, CHECK_PRESET, after, { ignoreFailures: true })).toBeNull();
    // A result saved since then is the outcome again.
    expect(knownOutcomeOf({ ...processed, updatedAt: "2026-11-03T11:00:00.000Z" }, CHECK_PRESET, after)).toEqual({
      status: "complete",
      warnings: [],
    });
  });

  it("processes everything else", () => {
    expect(knownOutcomeOf({ ...photo, processedStoragePath: "x.jpg", processedPreset: "autoexperten_dark" }, CHECK_PRESET, undefined)).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, photoId: "p_other" })).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, status: "error", errorCode: null })).toBeNull();
    expect(knownOutcomeOf(photo, CHECK_PRESET, { ...rejected, status: "checking" })).toBeNull();
  });
});

describe("PhotoChecks – jobs survive a reload", () => {
  const JOB = "0123456789abcdef0123456789abcdef";

  function journalEntry(photo: VehiclePhoto, jobId = JOB, userId = "u_anna") {
    return {
      userId,
      vehicleId: photo.vehicleId,
      photoId: photo.id,
      jobId,
      preset: CHECK_PRESET,
      startedAt: new Date().toISOString(),
      shotKey: photo.shotKey,
      shotOrder: photo.shotOrder,
      title: photo.title,
      takenAt: photo.takenAt,
    };
  }

  it("keeps the job id from the submit until the outcome is known", async () => {
    const job = gate();
    vi.mocked(processPhoto).mockImplementation(async (_photo, _preset, deps) => {
      deps.onSubmitted?.(JOB);
      await job.opened;
      return { warnings: [] };
    });
    const checks = started();
    save(savedPhoto("p_front", "front"));
    await vi.waitFor(() => expect(readJobJournal()).toHaveLength(1));
    expect(readJobJournal()[0]).toMatchObject({
      userId: "u_anna",
      photoId: "p_front",
      jobId: JOB,
      vehicleId: "veh_1",
      preset: CHECK_PRESET,
    });
    job.open();
    await vi.waitFor(() => expect(statusOf(checks, "p_front")).toBe("ok"));
    expect(readJobJournal()).toEqual([]);
  });

  it("resumes a check after a reload by polling its job instead of sending the photo again", async () => {
    const photo = savedPhoto("p_rear_left", "rear_left_45");
    env.photos = [photo];
    recordJob(journalEntry(photo));
    vi.mocked(processPhoto).mockRejectedValue(new ProcessingJobFailedError("vehicle_cropped", "Angeschnitten."));
    const reloaded = started();
    // Shown as being checked right away (not "aufgenommen" without a state).
    expect(statusOf(reloaded, "p_rear_left")).toBe("waiting");
    await vi.waitFor(() => expect(statusOf(reloaded, "p_rear_left")).toBe("retake"));
    expect(processPhoto).toHaveBeenCalledTimes(1);
    expect(vi.mocked(processPhoto).mock.calls[0]?.[2].resumeJobId).toBe(JOB);
    expect(reloaded.getSnapshot().checks.get("p_rear_left")).toMatchObject({ errorCode: "vehicle_cropped" });
    expect(readJobJournal()).toEqual([]);
  });

  it("resumes only the signed-in user's jobs – another user's are forgotten, never polled", async () => {
    const photo = savedPhoto("p_ben", "rear_left_45");
    env.photos = [photo];
    recordJob(journalEntry(photo, JOB, "u_ben"));
    const reloaded = started(); // Anna is signed in
    await flush();
    expect(reloaded.getSnapshot().checks.has("p_ben")).toBe(false);
    expect(processPhoto).not.toHaveBeenCalled();
    expect(readJobJournal()).toEqual([]);

    // Ben's job is not polled for Anna either when she opens the vehicle: her check sends the photo.
    recordJob(journalEntry(photo, JOB, "u_ben"));
    await reloaded.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(reloaded, "p_ben")).toBe("ok"));
    expect(vi.mocked(processPhoto).mock.calls[0]?.[2].resumeJobId).toBeNull();
  });

  it("keeps the job when the photos cannot be read right after a reload – and polls it on the next page open", async () => {
    const photo = savedPhoto("p_weak", "rear_left_45");
    env.photos = [photo];
    recordJob(journalEntry(photo));
    env.unreadable = 1;
    const reloaded = started();
    await vi.waitFor(() => expect(statusOf(reloaded, "p_weak")).toBe("error"));
    expect(processPhoto).not.toHaveBeenCalled();
    expect(readJobJournal().map((entry) => entry.jobId)).toEqual([JOB]);

    await reloaded.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(reloaded, "p_weak")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(1);
    expect(vi.mocked(processPhoto).mock.calls[0]?.[2].resumeJobId).toBe(JOB);
    expect(readJobJournal()).toEqual([]);
  });

  it("checks a new capture again on the next page open when its photo could not be read (nothing was sent)", async () => {
    const checks = started();
    env.unreadable = 1;
    save(savedPhoto("p_unread", "rear"));
    await vi.waitFor(() => expect(checks.getSnapshot().captures.get("veh_1/rear")?.photoId).toBe("p_unread"));
    await vi.waitFor(() => expect(checks.getSnapshot().checks.has("p_unread")).toBe(false));
    expect(processPhoto).not.toHaveBeenCalled();
    await checks.checkVehicle("veh_1");
    await vi.waitFor(() => expect(statusOf(checks, "p_unread")).toBe("ok"));
    expect(processPhoto).toHaveBeenCalledTimes(1);
  });

  it("lets the check wait with its job when neither the photos nor the processor can be reached", async () => {
    const photo = savedPhoto("p_down", "rear_left_45");
    env.photos = [photo];
    recordJob(journalEntry(photo));
    env.unreadable = 1;
    const reloaded = started();
    // The processor stops answering while the photos are read.
    status = { ...CONNECTED, connected: false, showroomSource: null };
    await vi.waitFor(() => expect(reloaded.getSnapshot().availability).toBe("unreachable"));
    await flush();
    expect(statusOf(reloaded, "p_down")).toBe("waiting");
    expect(processPhoto).not.toHaveBeenCalled();
    expect(readJobJournal().map((entry) => entry.jobId)).toEqual([JOB]);
  });

  it("forgets every kept job when the session ends", async () => {
    const photo = savedPhoto("p_end", "rear_left_45");
    env.photos = [photo];
    status = { ...CONNECTED, connected: false, showroomSource: null };
    recordJob(journalEntry(photo));
    const reloaded = started();
    await vi.waitFor(() => expect(reloaded.getSnapshot().availability).toBe("unreachable"));
    reloaded.stopAll();
    expect(readJobJournal()).toEqual([]);
    expect(reloaded.getSnapshot().checks.has("p_end")).toBe(false);
  });

  it("drops a resumed job whose photo was replaced meanwhile", async () => {
    const old = savedPhoto("p_old_front", "front", "2026-11-03T10:00:00.000Z");
    env.photos = [savedPhoto("p_new_front", "front", "2026-11-03T10:05:00.000Z")];
    recordJob(journalEntry(old));
    const reloaded = started();
    await vi.waitFor(() => expect(reloaded.getSnapshot().checks.has("p_old_front")).toBe(false));
    expect(processPhoto).not.toHaveBeenCalled();
    expect(readJobJournal()).toEqual([]);
  });

  it("keeps the job while the processor is not reachable and polls it once it is back", async () => {
    const photo = savedPhoto("p_sleep", "left_side");
    vi.mocked(processPhoto).mockImplementationOnce(async (_photo, _preset, deps) => {
      deps.onSubmitted?.(JOB);
      status = { ...CONNECTED, connected: false, showroomSource: null };
      throw new AppError("network");
    });
    const checks = started();
    save(photo);
    await vi.waitFor(() => expect(checks.getSnapshot().availability).toBe("unreachable"));
    await vi.waitFor(() => expect(statusOf(checks, "p_sleep")).toBe("waiting"));
    expect(readJobJournal().map((entry) => entry.jobId)).toEqual([JOB]);

    status = CONNECTED;
    await checks.refreshAvailability(true);
    await vi.waitFor(() => expect(statusOf(checks, "p_sleep")).toBe("ok"));
    expect(vi.mocked(processPhoto).mock.calls[1]?.[2].resumeJobId).toBe(JOB);
  });

  it("forgets the job of a photo a retake replaced", async () => {
    const job = gate();
    vi.mocked(processPhoto).mockImplementation(async (photo, _preset, deps) => {
      if (photo.id === "p_first") {
        deps.onSubmitted?.(JOB);
        await new Promise<void>((resolve, reject) => {
          void job.opened.then(resolve);
          deps.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
      }
      return { warnings: [] };
    });
    const checks = started();
    save(savedPhoto("p_first", "front", "2026-11-03T10:00:00.000Z"));
    await vi.waitFor(() => expect(readJobJournal()).toHaveLength(1));
    save(savedPhoto("p_second", "front", "2026-11-03T10:02:00.000Z"));
    await vi.waitFor(() => expect(statusOf(checks, "p_second")).toBe("ok"));
    expect(readJobJournal()).toEqual([]);
  });

  it("tolerates a storage that throws: the check still runs", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    try {
      vi.mocked(processPhoto).mockImplementation(async (_photo, _preset, deps) => {
        deps.onSubmitted?.(JOB);
        return { warnings: [] };
      });
      const checks = started();
      save(savedPhoto("p_blocked", "rear"));
      await vi.waitFor(() => expect(statusOf(checks, "p_blocked")).toBe("ok"));
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });
});

describe("PhotoChecks – availability while the checks are off", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const statusRequests = () =>
    vi.mocked(fetch).mock.calls.filter(([input]) => String(input).endsWith("/api/processing-status")).length;

  it("asks the processor again every minute while a page needs the checks – and checks what waited", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    status = { ...CONNECTED, connected: false, showroomSource: null };
    const checks = started();
    save(savedPhoto("p_slept", "rear"));
    await vi.waitFor(() => expect(checks.getSnapshot().availability).toBe("unreachable"));
    const unwatch = checks.watch();
    const before = statusRequests();
    vi.advanceTimersByTime(AVAILABILITY_POLL_MS);
    await vi.waitFor(() => expect(statusRequests()).toBe(before + 1));
    await flush();
    expect(checks.getSnapshot().availability).toBe("unreachable");

    // The dealer's PC is back.
    status = CONNECTED;
    vi.advanceTimersByTime(AVAILABILITY_POLL_MS);
    await vi.waitFor(() => expect(statusOf(checks, "p_slept")).toBe("ok"));
    // On again: no more polling.
    const settledRequests = statusRequests();
    vi.advanceTimersByTime(3 * AVAILABILITY_POLL_MS);
    await flush();
    expect(statusRequests()).toBe(settledRequests);
    unwatch();
  });

  it("does not poll without a page that needs the checks, or while the tab is hidden", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    status = { ...CONNECTED, connected: false, showroomSource: null };
    const checks = started();
    await checks.refreshAvailability(true);
    const before = statusRequests();
    vi.advanceTimersByTime(2 * AVAILABILITY_POLL_MS);
    await flush();
    expect(statusRequests()).toBe(before);

    const unwatch = checks.watch();
    unwatch();
    vi.advanceTimersByTime(2 * AVAILABILITY_POLL_MS);
    await flush();
    expect(statusRequests()).toBe(before);

    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    try {
      const hiddenWatch = checks.watch();
      vi.advanceTimersByTime(2 * AVAILABILITY_POLL_MS);
      await flush();
      expect(statusRequests()).toBe(before);
      hiddenWatch();
    } finally {
      visibility.mockRestore();
    }
  });
});

describe("checkOnOpen", () => {
  const photo = savedPhoto("p_front", "front");

  it("checks a template photo without a result, a check or a stored rejection", () => {
    expect(checkOnOpen(photo, AUTOEXPERTEN_STANDARD_TEMPLATE, undefined, false)).toBe("check");
  });

  it("never sends again a photo whose check ended – only polls a job that is still kept", () => {
    for (const state of ["waiting", "checking", "ok", "retake", "error"] as const) {
      expect(checkOnOpen(photo, AUTOEXPERTEN_STANDARD_TEMPLATE, { status: state }, false)).toBeNull();
    }
    expect(checkOnOpen(photo, AUTOEXPERTEN_STANDARD_TEMPLATE, { status: "error" }, true)).toBe("resume");
    expect(checkOnOpen(photo, AUTOEXPERTEN_STANDARD_TEMPLATE, { status: "retake" }, true)).toBeNull();
  });

  it("does not send a photo by itself whose rejection is kept with it (another device rejected it)", () => {
    const rejected = { ...photo, processingWarnings: [{ code: "rejected:vehicle_cropped", message: "angeschnitten" }] };
    expect(checkOnOpen(rejected, AUTOEXPERTEN_STANDARD_TEMPLATE, undefined, false)).toBeNull();
    expect(checkOnOpen(rejected, AUTOEXPERTEN_STANDARD_TEMPLATE, { status: "error" }, true)).toBeNull();
  });

  it("skips processed photos, extra photos and the demo samples' drawings", () => {
    const template = AUTOEXPERTEN_STANDARD_TEMPLATE;
    expect(checkOnOpen({ ...photo, processedStoragePath: "veh_1/front/p.jpg" }, template, undefined, false)).toBeNull();
    expect(checkOnOpen(savedPhoto("p_extra", "extra_01"), template, undefined, false)).toBeNull();
    expect(checkOnOpen({ ...photo, originalStoragePath: "static:/demo/shots/front.svg" }, template, undefined, false)).toBeNull();
  });
});

describe("shouldPollAvailability", () => {
  it("polls only while the processor is unreachable or the device offline, on a shown page in a visible tab", () => {
    expect(shouldPollAvailability("unreachable", true, true)).toBe(true);
    expect(shouldPollAvailability("offline", true, true)).toBe(true);
    expect(shouldPollAvailability("unreachable", false, true)).toBe(false);
    expect(shouldPollAvailability("unreachable", true, false)).toBe(false);
    // A deployment without a processor does not get one by waiting.
    for (const availability of ["on", "unknown", "access_code", "no_processor"] as const) {
      expect(shouldPollAvailability(availability, true, true)).toBe(false);
    }
  });
});
