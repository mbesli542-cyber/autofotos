import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ProcessingServiceError,
  RealImageProcessor,
  joinServiceUrl,
} from "./real-image-processor";
import type { ProcessingJob } from "./types";

const JOB: ProcessingJob = {
  jobId: "job_123",
  vehicleId: "veh_1",
  photoId: "photo_1",
  preset: "autoexperten_standard",
  status: "queued",
  progress: 0,
  createdAt: "2026-10-07T10:00:00.000Z",
  updatedAt: "2026-10-07T10:00:00.000Z",
  result: null,
  error: null,
};

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

describe("RealImageProcessor", () => {
  it("POSTs the job to {base}/jobs including the base path", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(JOB, 202));
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor", apiKey: null });

    const job = await processor.submit(
      { vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" },
      { userId: "user_1", shotKey: "front_left_45" },
    );

    expect(job).toEqual(JOB);
    const { url, init, headers } = lastCall();
    expect(url).toBe("https://host/processor/jobs");
    expect(init.method).toBe("POST");
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
    fetchMock.mockImplementation(async () => jsonResponse(JOB));

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: "secret" }).getJob("job_123");
    expect(lastCall().headers.get("Authorization")).toBe("Bearer secret");

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null }).getJob("job_123");
    expect(lastCall().headers.has("Authorization")).toBe(false);

    await new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: "" }).getJob("job_123");
    expect(lastCall().headers.has("Authorization")).toBe(false);
  });

  it("GETs {base}/jobs/{id} with an encoded id", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(JOB));
    const processor = new RealImageProcessor({ baseUrl: "https://host/processor/", apiKey: null });

    await expect(processor.getJob("job_123")).resolves.toEqual(JOB);
    const { url, init } = lastCall();
    expect(url).toBe("https://host/processor/jobs/job_123");
    expect(init.method).toBe("GET");
  });

  it("returns null when the service answers 404", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: { code: "not_found", message: "x" } }, 404));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });

    await expect(processor.getJob("missing")).resolves.toBeNull();
  });

  it("throws ProcessingServiceError for other non-OK responses", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({}, 500));
    const processor = new RealImageProcessor({ baseUrl: "http://localhost:8000", apiKey: null });
    const error = await processor.getJob("job_123").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ProcessingServiceError);
    expect((error as ProcessingServiceError).status).toBe(500);

    fetchMock.mockResolvedValueOnce(jsonResponse({}, 401));
    await expect(
      processor.submit(
        { vehicleId: "veh_1", photoId: "photo_1", preset: "autoexperten_standard" },
        { userId: null },
      ),
    ).rejects.toBeInstanceOf(ProcessingServiceError);
  });
});
