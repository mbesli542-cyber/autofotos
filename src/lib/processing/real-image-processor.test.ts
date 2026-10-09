import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ProcessingServiceError,
  RealImageProcessor,
  joinServiceUrl,
} from "./real-image-processor";

const JOB_ID = "0123456789abcdef0123456789abcdef";

/** A JobResponse exactly as the processor sends it. */
function processorJob(overrides: Record<string, unknown> = {}) {
  return {
    jobId: JOB_ID,
    vehicleId: "veh_1",
    photoId: "photo_1",
    preset: "autoexperten_standard",
    status: "queued",
    progress: 0,
    createdAt: "2026-10-07T10:00:00.000Z",
    updatedAt: "2026-10-07T10:00:00.000Z",
    result: null,
    error: null,
    warnings: [],
    metadata: {},
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

function lastCall(): { url: string; init: RequestInit; headers: Headers } {
  const [input, init = {}] = fetchMock.mock.calls.at(-1) ?? [];
  return { url: String(input), init, headers: new Headers(init.headers) };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("joinServiceUrl", () => {
  it("appends paths to a plain origin", () => {
    expect(joinServiceUrl("http://localhost:8000", "/jobs").href).toBe("http://localhost:8000/jobs");
  });

  it("keeps a path prefix with or without trailing slash", () => {
    expect(joinServiceUrl("https://host/processor", "/jobs").href).toBe("https://host/processor/jobs");
    expect(joinServiceUrl("https://host/processor/", "jobs/abc").href).toBe(
      "https://host/processor/jobs/abc",
    );
  });

  it("keeps query parameters of the base URL", () => {
    expect(joinServiceUrl("https://host/api?code=k", "/jobs").href).toBe("https://host/api/jobs?code=k");
  });
});

describe("RealImageProcessor – Supabase contract", () => {
  it("POSTs the job to {base}/jobs including the base path", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(processorJob(), 202));
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor", apiKey: null });

    const job = await processor.submit(
      { vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" },
      { userId: "user_1", shotKey: "front_left_45" },
    );

    expect(job).toMatchObject({ jobId: JOB_ID, status: "queued", vehicleId: "veh_1", photoId: "photo_1" });
    const { url, init, headers } = lastCall();
    expect(url).toBe("https://host/processor/jobs");
    expect(init.method).toBe("POST");
    expect(init.redirect).toBe("manual");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      vehicleId: "veh_1",
      photoId: "photo_1",
      preset: "autoexperten_standard",
      userId: "user_1",
      shotKey: "front_left_45",
    });
  });

  it("sends the Bearer header only when an API key is configured", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(processorJob()));

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: "secret" }).getJob(JOB_ID);
    expect(lastCall().headers.get("Authorization")).toBe("Bearer secret");

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null }).getJob(JOB_ID);
    expect(lastCall().headers.has("Authorization")).toBe(false);

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: "" }).getJob(JOB_ID);
    expect(lastCall().headers.has("Authorization")).toBe(false);
  });

  it("GETs {base}/jobs/{id} and maps a stored result", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        processorJob({
          status: "complete",
          progress: 1,
          result: { kind: "stored", processedStoragePath: "veh_1/autoexperten_standard/front/photo_1.jpg" },
        }),
      ),
    );
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor/", apiKey: null });

    await expect(processor.getJob(JOB_ID)).resolves.toMatchObject({
      status: "complete",
      result: { kind: "stored", processedStoragePath: "veh_1/autoexperten_standard/front/photo_1.jpg" },
    });
    const { url, init } = lastCall();
    expect(url).toBe(`https://host/processor/jobs/${JOB_ID}`);
    expect(init.method).toBe("GET");
  });

  it("never forwards invalid job ids", async () => {
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    await expect(processor.getJob("../health")).resolves.toBeNull();
    await expect(processor.fetchResult("job_123")).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns null when the service answers 404", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "not_found", message: "x" } }, 404));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });

    await expect(processor.getJob(JOB_ID)).resolves.toBeNull();
  });

  it("throws ProcessingServiceError with the processor's code and German message", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 500));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const error = await processor.getJob(JOB_ID).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ProcessingServiceError);
    expect((error as ProcessingServiceError).status).toBe(500);

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "busy", message: "Der Bildverarbeitungs-Service ist ausgelastet." } }, 503),
    );
    const busy = await processor
      .submit({ vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" }, { userId: null })
      .catch((reason: unknown) => reason);
    expect(busy).toBeInstanceOf(ProcessingServiceError);
    expect((busy as ProcessingServiceError).status).toBe(503);
    expect((busy as ProcessingServiceError).code).toBe("busy");
    expect((busy as ProcessingServiceError).processorMessage).toBe("Der Bildverarbeitungs-Service ist ausgelastet.");
  });

  it("reports network failures as ProcessingServiceError", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const error = await processor.getJob(JOB_ID).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ProcessingServiceError);
    expect((error as ProcessingServiceError).reason).toBe("network");
  });

  it("rejects unusable job responses", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ jobId: "x", status: "done" }, 202));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const error = await processor
      .submit({ vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" }, { userId: null })
      .catch((reason: unknown) => reason);
    expect((error as ProcessingServiceError).reason).toBe("invalid_response");
  });
});

describe("RealImageProcessor – upload flow (demo mode)", () => {
  it("POSTs multipart file, preset and shotKey to {base}/jobs/upload with the key", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(processorJob({ vehicleId: null, photoId: null }), 202));
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor", apiKey: "secret" });
    const file = new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" });

    const job = await processor.submitUpload({
      file,
      fileName: "front_left_45.jpg",
      preset: "autoexperten_standard",
      shotKey: "front_left_45",
    });

    expect(job).toMatchObject({ jobId: JOB_ID, status: "queued", vehicleId: null, photoId: null });
    const { url, init, headers } = lastCall();
    expect(url).toBe("https://host/processor/jobs/upload");
    expect(init.method).toBe("POST");
    expect(headers.get("Authorization")).toBe("Bearer secret");
    // The multipart boundary is set by fetch – never a JSON content type.
    expect(headers.has("Content-Type")).toBe(false);
    const form = init.body as FormData;
    expect([...form.keys()]).toEqual(["file", "preset", "shotKey"]);
    expect(form.get("preset")).toBe("autoexperten_standard");
    expect(form.get("shotKey")).toBe("front_left_45");
    const sent = form.get("file") as File;
    expect(sent.name).toBe("front_left_45.jpg");
    expect(sent.size).toBe(3);
  });

  it("drops the processor's resultUrl from file results", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        processorJob({
          vehicleId: null,
          photoId: null,
          status: "complete",
          progress: 1,
          result: { kind: "file", resultUrl: `/jobs/${JOB_ID}/result`, width: 3200, height: 2400, bytes: 1234 },
          metadata: { showroomSource: "plates", showroomPlaceholder: false },
        }),
      ),
    );
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const job = await processor.getJob(JOB_ID);
    expect(job?.result).toEqual({ kind: "file", width: 3200, height: 2400, bytes: 1234 });
    expect(JSON.stringify(job)).not.toContain("resultUrl");
  });

  it("streams the result JPEG from {base}/jobs/{id}/result", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 200,
        headers: { "Content-Type": "image/jpeg", "Content-Length": "4" },
      }),
    );
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor", apiKey: "secret" });

    const file = await processor.fetchResult(JOB_ID);
    expect(file?.contentLength).toBe("4");
    const bytes = new Uint8Array(await new Response(file?.body).arrayBuffer());
    expect([...bytes]).toEqual([1, 2, 3, 4]);
    const { url, headers } = lastCall();
    expect(url).toBe(`https://host/processor/jobs/${JOB_ID}/result`);
    expect(headers.get("Accept")).toBe("image/jpeg");
    expect(headers.get("Authorization")).toBe("Bearer secret");
  });

  it("maps 404 to null and 409 (not ready) to an error", async () => {
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "not_found", message: "x" } }, 404));
    await expect(processor.fetchResult(JOB_ID)).resolves.toBeNull();

    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "not_ready", message: "Das Ergebnis ist noch nicht fertig." } }, 409),
    );
    const error = await processor.fetchResult(JOB_ID).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ProcessingServiceError);
    expect((error as ProcessingServiceError).status).toBe(409);
  });

  it("refuses result files that are not JPEG", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const error = await processor.fetchResult(JOB_ID).catch((reason: unknown) => reason);
    expect((error as ProcessingServiceError).reason).toBe("invalid_response");
  });
});

describe("RealImageProcessor – health", () => {
  it("GETs {base}/health with the Bearer key and parses the showroom state", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        status: "ok",
        version: "1",
        modelLoaded: true,
        modelError: false,
        showroomSource: "plates",
        showroomMasterError: null,
        presetError: null,
      }),
    );
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor", apiKey: "secret" });

    await expect(processor.getHealth()).resolves.toEqual({
      ok: true,
      authorized: true,
      modelError: false,
      showroomSource: "plates",
      showroomMasterError: false,
      presetError: false,
    });
    const { url, headers } = lastCall();
    expect(url).toBe("https://host/processor/health");
    expect(headers.get("Authorization")).toBe("Bearer secret");
  });

  it("detects a rejected key (only status + version returned)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok", version: "1" }));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: "wrong" });
    await expect(processor.getHealth()).resolves.toMatchObject({ ok: true, authorized: false });
  });

  it("reports a model error (503) and unreachable services", async () => {
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "error", modelError: true }, 503));
    await expect(processor.getHealth()).resolves.toMatchObject({ ok: false, modelError: true });

    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(processor.getHealth()).resolves.toBeNull();
  });
});
