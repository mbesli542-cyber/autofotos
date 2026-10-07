/**
 * RealImageProcessor – adapter for the external processing service
 * (processor/, Python/FastAPI).
 *
 * Connected by setting environment variables only:
 *
 *   IMAGE_PROCESSOR=real
 *   IMAGE_PROCESSING_API_URL=https://processing.example.com   (may contain a
 *                                       path prefix, e.g. https://host/processor)
 *   IMAGE_PROCESSING_API_KEY=…            (server-side secret)
 *
 * Expected service API (paths are appended to the base URL):
 *   POST {url}/jobs      body: ProcessPhotoRequest & { userId, shotKey }
 *                        → { jobId, status }
 *   GET  {url}/jobs/:id  → ProcessingJob   (404 → unknown job)
 *
 * The service reads the original from `vehicle-originals`, writes the result
 * to `vehicle-processed/{vehicleId}/{preset}/{shotKey}/{photoId}.jpg`, sets
 * `vehicle_photos.processed_storage_path` (service role) and returns
 * `result: { kind: "stored", processedStoragePath }`.
 * Originals are never modified.
 */
import type {
  ImageProcessor,
  ProcessPhotoRequest,
  ProcessingContext,
  ProcessingJob,
} from "./types";

export interface RealImageProcessorConfig {
  baseUrl: string;
  apiKey: string | null;
  timeoutMs?: number;
}

/**
 * Appends `path` to the service base URL while keeping any path prefix of
 * the base (`new URL("/jobs", "https://host/processor")` would drop it).
 * Query parameters of the base URL (e.g. gateway keys) are kept.
 */
export function joinServiceUrl(baseUrl: string, path: string): URL {
  const url = new URL(baseUrl);
  const prefix = url.pathname.replace(/\/+$/, "");
  const suffix = path.replace(/^\/+/, "");
  url.pathname = suffix ? `${prefix}/${suffix}` : prefix || "/";
  url.hash = "";
  return url;
}

export class RealImageProcessor implements ImageProcessor {
  readonly name = "real";

  constructor(private readonly config: RealImageProcessorConfig) {}

  async submit(
    request: ProcessPhotoRequest,
    context: ProcessingContext,
  ): Promise<ProcessingJob> {
    const response = await this.fetchJson("/jobs", {
      method: "POST",
      body: JSON.stringify({ ...request, userId: context.userId, shotKey: context.shotKey }),
    });
    return response as ProcessingJob;
  }

  async getJob(jobId: string): Promise<ProcessingJob | null> {
    try {
      return (await this.fetchJson(`/jobs/${encodeURIComponent(jobId)}`, {
        method: "GET",
      })) as ProcessingJob;
    } catch (error) {
      if (error instanceof ProcessingServiceError && error.status === 404) return null;
      throw error;
    }
  }

  private async fetchJson(path: string, init: { method: "GET" | "POST"; body?: string }): Promise<unknown> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs ?? 15_000);
    try {
      const response = await fetch(joinServiceUrl(this.config.baseUrl, path), {
        ...init,
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {}),
        },
        cache: "no-store",
      });
      if (!response.ok) throw new ProcessingServiceError(response.status);
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }
}

export class ProcessingServiceError extends Error {
  constructor(readonly status: number) {
    super(`Processing service responded with ${status}`);
    this.name = "ProcessingServiceError";
  }
}
