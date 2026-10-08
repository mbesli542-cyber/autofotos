/**
 * RealImageProcessor – the ONLY code that talks HTTP to the external
 * processing service (processor/, Python/FastAPI). Server side only.
 *
 * Connected by setting environment variables only (see processor-config.ts):
 *
 *   IMAGE_PROCESSOR=real
 *   IMAGE_PROCESSING_API_URL=https://processing.example.com   (may contain a
 *                                       path prefix, e.g. https://host/processor)
 *   IMAGE_PROCESSING_API_KEY=…            (server-side secret, Bearer token)
 *
 * Service API (paths are appended to the base URL):
 *   POST {url}/jobs                JSON ProcessPhotoRequest & { userId, shotKey } → 202 JobResponse
 *                                  (Supabase mode: the service reads the original from
 *                                  `vehicle-originals`, writes `vehicle-processed/…`
 *                                  and returns result { kind: "stored", processedStoragePath })
 *   POST {url}/jobs/upload         multipart file, preset, shotKey → 202 JobResponse
 *                                  (demo mode: result { kind: "file", resultUrl, … })
 *   GET  {url}/jobs/{id}           JobResponse (404 → unknown job)
 *   GET  {url}/jobs/{id}/result    image/jpeg (409 → not ready, 404 → unknown)
 *   GET  {url}/health              status, model and showroom state
 * Originals are never modified.
 */
import { asMessage, parseProcessorHealth, parseProcessorJob } from "./processor-contract";
import {
  isValidProcessingJobId,
  type ImageProcessor,
  type ProcessPhotoRequest,
  type ProcessingContext,
  type ProcessingJob,
  type ProcessorHealthReport,
  type ProcessorResultFile,
  type UploadJobInput,
} from "./types";

export interface RealImageProcessorConfig {
  baseUrl: string;
  apiKey: string | null;
  /** Timeout for JSON calls (job submit/status). */
  timeoutMs?: number;
  /** Timeout for photo uploads. */
  uploadTimeoutMs?: number;
  /** Timeout until the result file's headers arrive (the body then streams). */
  fileTimeoutMs?: number;
}

export const HEALTH_TIMEOUT_MS = 4_000;

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

/**
 * A failed call to the processor.
 * - `status`: HTTP status of the processor (0 for network errors/timeouts)
 * - `code` / `processorMessage`: from its `{ error: { code, message } }` body
 *   (the message is German and may be shown to users)
 */
export class ProcessingServiceError extends Error {
  constructor(
    readonly status: number,
    readonly detail: {
      code?: string | null;
      processorMessage?: string | null;
      reason?: "http" | "timeout" | "network" | "invalid_response";
    } = {},
  ) {
    super(`Processing service error (${detail.reason ?? "http"}, status ${status})`);
    this.name = "ProcessingServiceError";
  }

  get code(): string | null {
    return this.detail.code ?? null;
  }

  get processorMessage(): string | null {
    return this.detail.processorMessage ?? null;
  }

  get reason(): "http" | "timeout" | "network" | "invalid_response" {
    return this.detail.reason ?? "http";
  }
}

async function errorFromResponse(response: Response): Promise<ProcessingServiceError> {
  let code: string | null = null;
  let processorMessage: string | null = null;
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown } } | null;
    const rawCode = body?.error?.code;
    code = typeof rawCode === "string" && /^[a-z_]{1,40}$/.test(rawCode) ? rawCode : null;
    processorMessage = asMessage(body?.error?.message);
  } catch {
    // not JSON – keep the status only
  }
  return new ProcessingServiceError(response.status, { code, processorMessage, reason: "http" });
}

interface CallOptions {
  method: "GET" | "POST";
  body?: string | FormData;
  accept: string;
  timeoutMs: number;
  signal?: AbortSignal;
}

export class RealImageProcessor implements ImageProcessor {
  readonly name = "real";

  constructor(private readonly config: RealImageProcessorConfig) {}

  async submit(request: ProcessPhotoRequest, context: ProcessingContext): Promise<ProcessingJob> {
    const response = await this.call("/jobs", {
      method: "POST",
      body: JSON.stringify({ ...request, userId: context.userId, shotKey: context.shotKey }),
      accept: "application/json",
      timeoutMs: this.config.timeoutMs ?? 15_000,
    });
    return this.readJob(response);
  }

  async submitUpload(input: UploadJobInput, signal?: AbortSignal): Promise<ProcessingJob> {
    // Only the three contract fields are forwarded.
    const form = new FormData();
    form.append("file", input.file, input.fileName);
    form.append("preset", input.preset);
    form.append("shotKey", input.shotKey);
    const response = await this.call("/jobs/upload", {
      method: "POST",
      body: form,
      accept: "application/json",
      timeoutMs: this.config.uploadTimeoutMs ?? 60_000,
      signal,
    });
    return this.readJob(response);
  }

  async getJob(jobId: string): Promise<ProcessingJob | null> {
    if (!isValidProcessingJobId(jobId)) return null;
    try {
      const response = await this.call(`/jobs/${encodeURIComponent(jobId)}`, {
        method: "GET",
        accept: "application/json",
        timeoutMs: this.config.timeoutMs ?? 15_000,
      });
      const job = await this.readJob(response);
      if (job.jobId !== jobId) throw new ProcessingServiceError(response.status, { reason: "invalid_response" });
      return job;
    } catch (error) {
      if (error instanceof ProcessingServiceError && error.status === 404) return null;
      throw error;
    }
  }

  async fetchResult(jobId: string, signal?: AbortSignal): Promise<ProcessorResultFile | null> {
    if (!isValidProcessingJobId(jobId)) return null;
    let response: Response;
    try {
      response = await this.call(`/jobs/${encodeURIComponent(jobId)}/result`, {
        method: "GET",
        accept: "image/jpeg",
        timeoutMs: this.config.fileTimeoutMs ?? 30_000,
        signal,
      });
    } catch (error) {
      if (error instanceof ProcessingServiceError && error.status === 404) return null;
      throw error;
    }
    const type = response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase();
    if (type !== "image/jpeg" || !response.body) {
      await response.body?.cancel().catch(() => undefined);
      throw new ProcessingServiceError(response.status, { reason: "invalid_response" });
    }
    const length = response.headers.has("content-encoding") ? null : response.headers.get("content-length");
    return { body: response.body, contentLength: length && /^\d+$/.test(length) ? length : null };
  }

  async getHealth(timeoutMs: number = HEALTH_TIMEOUT_MS): Promise<ProcessorHealthReport | null> {
    let response: Response;
    try {
      response = await this.call("/health", { method: "GET", accept: "application/json", timeoutMs });
    } catch (error) {
      // 503 = model cannot be loaded; the body still carries the details.
      if (!(error instanceof ProcessingServiceError) || error.status !== 503) return null;
      return { ok: false, authorized: true, modelError: true, showroomSource: null, showroomMasterError: false, presetError: false };
    }
    try {
      return parseProcessorHealth(response.status, await response.json());
    } catch {
      return null;
    }
  }

  private async readJob(response: Response): Promise<ProcessingJob> {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ProcessingServiceError(response.status, { reason: "invalid_response" });
    }
    const job = parseProcessorJob(body);
    if (!job) throw new ProcessingServiceError(response.status, { reason: "invalid_response" });
    return job;
  }

  /**
   * One request to the processor. Non-OK answers throw ProcessingServiceError
   * (with the processor's code and German message). The timeout covers the
   * time until the response headers arrive; a body is then streamed and
   * cancelled together with `signal`.
   */
  private async call(path: string, options: CallOptions): Promise<Response> {
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), options.timeoutMs);
    const signal = options.signal ? AbortSignal.any([options.signal, timeout.signal]) : timeout.signal;
    let response: Response;
    try {
      response = await fetch(joinServiceUrl(this.config.baseUrl, path), {
        method: options.method,
        body: options.body,
        signal,
        cache: "no-store",
        // Never follow redirects to other hosts/paths with our credentials.
        redirect: "manual",
        headers: {
          Accept: options.accept,
          ...(typeof options.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...(this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {}),
        },
      });
    } catch (error) {
      throw new ProcessingServiceError(0, {
        reason: timeout.signal.aborted ? "timeout" : "network",
        processorMessage: null,
        code: error instanceof Error && error.name === "AbortError" ? "aborted" : null,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) {
      const error = await errorFromResponse(response);
      if (!response.bodyUsed) await response.body?.cancel().catch(() => undefined);
      throw error;
    }
    return response;
  }
}
