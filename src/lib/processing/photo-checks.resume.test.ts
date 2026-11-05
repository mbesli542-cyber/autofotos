/**
 * A check survives a reload – the whole chain from the job kept on the device
 * (job-journal.ts) through processPhoto and the app's processing API (fetch
 * stubbed like the routes answer), including a processor that was restarted
 * meanwhile (404 for the known job → submitted once more).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VehiclePhoto } from "@/lib/domain/types";
import { AUTOEXPERTEN_STANDARD_TEMPLATE } from "@/lib/shots/shot-template";
import { readJobJournal, recordJob } from "./job-journal";
import { CHECK_PRESET, PhotoChecks } from "./photo-checks";
import type { ProcessingJob, ProcessingStatus } from "./types";

const env = vi.hoisted(() => ({
  photos: [] as unknown[],
  saved: [] as unknown[],
}));

vi.mock("@/lib/app-services", () => ({
  getAppServices: () => ({
    backend: {
      mode: "demo",
      data: {
        listPhotos: async () => env.photos,
        resolvePhotoUrls: async (photos: unknown[]) =>
          (photos as { id: string }[]).map((photo) => ({
            ...photo,
            urls: { original: `blob:${photo.id}`, thumbnail: `blob:${photo.id}`, processed: null },
          })),
        getVehicle: async () => ({ status: "capturing" }),
        saveProcessedPhoto: async (input: unknown) => {
          env.saved.push(input);
        },
        recordProcessedPhoto: async () => undefined,
      },
    },
    template: AUTOEXPERTEN_STANDARD_TEMPLATE,
    uploads: { onUploaded: () => () => {} },
    authStore: {
      getSnapshot: () => ({ status: "authenticated", user: { id: "u_anna", email: "anna@autoexperten-rn.de" } }),
      subscribe: () => () => {},
    },
  }),
}));

vi.mock("./prepare-upload", () => ({
  loadOriginalPhoto: async () => new Blob(["original"], { type: "image/jpeg" }),
  prepareProcessingUpload: async (original: Blob) => original,
}));

vi.mock("@/lib/workflow/vehicle-workflow", () => ({ markProcessedIfDone: vi.fn(async () => false) }));

const OLD_JOB = "0000000000000000000000000000000a";
const NEW_JOB = "0000000000000000000000000000000b";

const STATUS: ProcessingStatus = {
  connected: true,
  processor: "real",
  showroomSource: "plates",
  showroomError: null,
  connectionError: null,
  accessCodeRequired: false,
  dataBackend: "demo",
};

const PHOTO: VehiclePhoto = {
  id: "p_rear_left",
  vehicleId: "veh_1",
  shotKey: "rear_left_45",
  shotOrder: 6,
  title: "Hinten links (45°)",
  originalStoragePath: "veh_1/rear_left_45/p_rear_left.jpg",
  processedStoragePath: null,
  processedPreset: null,
  processingWarnings: [],
  thumbnailStoragePath: null,
  width: 4000,
  height: 3000,
  takenAt: "2026-11-05T09:00:00.000Z",
  createdAt: "2026-11-05T09:00:00.000Z",
  updatedAt: "2026-11-05T09:00:00.000Z",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function job(jobId: string, patch: Partial<ProcessingJob>): ProcessingJob {
  return {
    jobId,
    vehicleId: null,
    photoId: null,
    preset: CHECK_PRESET,
    status: "complete",
    progress: 1,
    createdAt: "",
    updatedAt: "",
    result: { kind: "file", width: 2400, height: 1800, bytes: 4 },
    error: null,
    errorCode: null,
    warnings: [],
    ...patch,
  };
}

/** The processor as the app's routes pass it on; `known`: the jobs it still has in memory. */
function stubProcessor(known: Map<string, ProcessingJob>) {
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      requests.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/api/processing-status")) return json(STATUS);
      if (url.endsWith("/api/process-upload")) {
        known.set(NEW_JOB, job(NEW_JOB, {}));
        return json({ jobId: NEW_JOB, status: "queued" }, 202);
      }
      const result = /\/api\/process-job\/([a-f0-9]{32})\/result$/.exec(url);
      if (result) return new Response("jpeg", { status: 200, headers: { "Content-Type": "image/jpeg" } });
      const poll = /\/api\/process-job\/([a-f0-9]{32})$/.exec(url);
      const found = poll ? known.get(poll[1] as string) : undefined;
      if (found) return json(found);
      return json({ error: { code: "not_found", message: "Auftrag wurde nicht gefunden oder ist abgelaufen." } }, 404);
    }),
  );
  return requests;
}

function rememberJob(jobId: string) {
  recordJob({
    vehicleId: PHOTO.vehicleId,
    photoId: PHOTO.id,
    jobId,
    preset: CHECK_PRESET,
    startedAt: new Date().toISOString(),
    shotKey: PHOTO.shotKey,
    shotOrder: PHOTO.shotOrder,
    title: PHOTO.title,
    takenAt: PHOTO.takenAt,
  });
}

function reloadedChecks(): PhotoChecks {
  const checks = new PhotoChecks();
  checks.start();
  return checks;
}

beforeEach(() => {
  window.localStorage.clear();
  env.photos = [PHOTO];
  env.saved = [];
  vi.unstubAllGlobals();
});

describe("PhotoChecks after a reload (whole chain)", () => {
  it("fetches the answer of the job submitted before the reload – the photo is not sent again", async () => {
    const requests = stubProcessor(
      new Map([[OLD_JOB, job(OLD_JOB, { status: "failed", result: null, error: "Angeschnitten.", errorCode: "vehicle_cropped" })]]),
    );
    rememberJob(OLD_JOB);
    const checks = reloadedChecks();
    await vi.waitFor(() => expect(checks.getSnapshot().checks.get(PHOTO.id)?.status).toBe("retake"));
    expect(checks.getSnapshot().checks.get(PHOTO.id)).toMatchObject({ errorCode: "vehicle_cropped", message: "Angeschnitten." });
    expect(requests.filter((request) => request.includes("/api/process-upload"))).toEqual([]);
    expect(readJobJournal()).toEqual([]);
  });

  it("submits the photo once more when the processor was restarted (404) and saves that result", async () => {
    const requests = stubProcessor(new Map());
    rememberJob(OLD_JOB);
    const checks = reloadedChecks();
    await vi.waitFor(() => expect(checks.getSnapshot().checks.get(PHOTO.id)?.status).toBe("ok"));
    expect(requests.filter((request) => !request.endsWith("/api/processing-status"))).toEqual([
      `GET /api/process-job/${OLD_JOB}`,
      "POST /api/process-upload",
      `GET /api/process-job/${NEW_JOB}`,
      `GET /api/process-job/${NEW_JOB}/result`,
    ]);
    expect(env.saved).toHaveLength(1);
    expect(env.saved[0]).toMatchObject({ preset: CHECK_PRESET, photo: { id: PHOTO.id } });
    expect(readJobJournal()).toEqual([]);
  });
});
