import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { processingErrorResponse } from "@/lib/api/processing-errors";
import { AppError, toUserMessage } from "@/lib/errors";
import { PRESET_UNAVAILABLE_MESSAGE } from "./presets";
import {
  AccessCodeRequiredError,
  ProcessingNotConnectedError,
  downloadProcessingResult,
  submitPhotoProcessing,
  submitUploadProcessing,
  waitForProcessingJob,
} from "./processing-client";
import { RealImageProcessor } from "./real-image-processor";
import { PROCESSING_BUSY_CODE, type ProcessingJobStatus } from "./types";

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
  vi.restoreAllMocks();
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

  it("keeps waiting for a free slot for about 10 minutes, then gives up with the German busy message", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    fetchMock.mockImplementation(async () => busy());
    const startedAt = Date.now();

    const pending = submitPhotoProcessing(REQUEST);
    const outcome = expect(pending).rejects.toThrow("Die Bildbearbeitung ist gerade ausgelastet.");
    await vi.runAllTimersAsync();
    await outcome;
    const waited = Date.now() - startedAt;
    expect(waited).toBeGreaterThan(9 * 60_000);
    expect(waited).toBeLessThanOrEqual(10 * 60_000);
    // Growing waits (at most a minute): far more attempts than the old four, without hammering.
    expect(fetchMock.mock.calls.length).toBeGreaterThan(10);
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(18);
  });

  it("spreads the busy retries randomly so waiting devices do not retry in lockstep", async () => {
    async function firstRetryAfter(random: number): Promise<number> {
      vi.spyOn(Math, "random").mockReturnValue(random);
      fetchMock
        .mockResolvedValueOnce(busy())
        .mockResolvedValueOnce(jsonResponse({ jobId: "job_1", status: "queued" }, 202));
      const startedAt = Date.now();
      const pending = submitPhotoProcessing(REQUEST);
      await vi.runAllTimersAsync();
      await pending;
      return Date.now() - startedAt;
    }
    expect(await firstRetryAfter(0)).toBe(1_500);
    expect(await firstRetryAfter(0.999)).toBeCloseTo(3_000, -1);
  });

  it("does not retry other errors", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "processing_unavailable", message: "nicht erreichbar" } }, 502),
    );

    await expect(submitPhotoProcessing(REQUEST)).rejects.toBeInstanceOf(AppError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("upload flow (demo mode)", () => {
  const JOB_ID = "0123456789abcdef0123456789abcdef";
  const file = new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" });

  it("POSTs multipart to /api/process-upload with the URI-encoded access code and retries while busy", async () => {
    fetchMock
      .mockResolvedValueOnce(busy())
      .mockResolvedValueOnce(jsonResponse({ jobId: JOB_ID, status: "queued" }, 202));

    const pending = submitUploadProcessing(
      { file, preset: "autoexperten_standard", shotKey: "cockpit" },
      { accessCode: " Schlüssel " },
    );
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toEqual({ jobId: JOB_ID, status: "queued" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init = {}] = fetchMock.mock.calls[1] ?? [];
    expect(url).toBe("/api/process-upload");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("x-processing-access-code")).toBe(encodeURIComponent("Schlüssel"));
    const form = init.body as FormData;
    expect(form.get("preset")).toBe("autoexperten_standard");
    expect(form.get("shotKey")).toBe("cockpit");
    expect((form.get("file") as File).size).toBe(3);
  });

  it("re-sends the photo copy at most about once a minute while the processor stays busy", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const sentAt: number[] = [];
    fetchMock.mockImplementation(async () => {
      sentAt.push(Date.now());
      return busy();
    });

    const pending = submitUploadProcessing({ file, preset: "autoexperten_standard", shotKey: "front" });
    const outcome = expect(pending).rejects.toThrow("Die Bildbearbeitung ist gerade ausgelastet.");
    await vi.runAllTimersAsync();
    await outcome;
    // Every attempt uploads the copy (up to 4 MB) again: 17 attempts in the 10 minutes, not ~30.
    expect(sentAt.length).toBeLessThanOrEqual(18);
    expect(sentAt.at(-1)! - sentAt[0]!).toBeGreaterThan(9 * 60_000);
    const gaps = sentAt.slice(1).map((time, index) => time - sentAt[index]!);
    expect(gaps.slice(5).every((gap) => gap >= 30_000)).toBe(true);
  });

  it("raises AccessCodeRequiredError for a missing or wrong code", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "access_code_required", message: "Der Zugangscode für die Bildbearbeitung ist ungültig." } }, 401),
    );
    const error = await submitUploadProcessing({ file, preset: "autoexperten_standard", shotKey: "front" }).catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(AccessCodeRequiredError);
    expect((error as Error).message).toBe("Der Zugangscode für die Bildbearbeitung ist ungültig.");
  });

  it("raises ProcessingNotConnectedError when no processor is connected", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "processing_not_connected", message: "Echte Showroom-Bearbeitung ist noch nicht verbunden." } }, 503),
    );
    const error = await submitUploadProcessing({ file, preset: "autoexperten_standard", shotKey: "front" }).catch(
      (reason: unknown) => reason,
    );
    expect(error).toBeInstanceOf(ProcessingNotConnectedError);
    expect((error as Error).message).toBe("Echte Showroom-Bearbeitung ist noch nicht verbunden.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("downloads the result JPEG from the app route and retries while not ready", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ error: { code: "not_ready", message: "Das Ergebnis ist noch nicht fertig." } }, 409))
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "image/jpeg" } }));

    const pending = downloadProcessingResult(JOB_ID, { accessCode: "abc" });
    await vi.runAllTimersAsync();
    const blob = await pending;
    expect(blob.size).toBe(3);
    expect(blob.type).toBe("image/jpeg");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      `/api/process-job/${JOB_ID}/result`,
      `/api/process-job/${JOB_ID}/result`,
    ]);
  });

  it("downloads the result through a dropped connection and a gateway error", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(
        jsonResponse({ error: { code: "processing_unavailable", message: "Die Bildbearbeitung ist derzeit nicht erreichbar." } }, 502),
      )
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "image/jpeg" } }));

    const pending = downloadProcessingResult(JOB_ID);
    await vi.runAllTimersAsync();
    expect((await pending).size).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("downloads the result again when the connection drops while the file streams", async () => {
    /** Headers arrive, then the connection breaks while the body is read. */
    const dropped = () => {
      const response = new Response(new Uint8Array([9]), { status: 200, headers: { "Content-Type": "image/jpeg" } });
      vi.spyOn(response, "blob").mockRejectedValue(new TypeError("network error"));
      return response;
    };
    fetchMock
      .mockResolvedValueOnce(dropped())
      .mockResolvedValueOnce(dropped())
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "image/jpeg" } }));

    const pending = downloadProcessingResult(JOB_ID);
    await vi.runAllTimersAsync();
    expect((await pending).size).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("gives up on a download that keeps breaking off after about a minute", async () => {
    fetchMock.mockImplementation(async () => {
      const response = new Response(new Uint8Array([9]), { status: 200, headers: { "Content-Type": "image/jpeg" } });
      vi.spyOn(response, "blob").mockRejectedValue(new TypeError("network error"));
      return response;
    });
    const startedAt = Date.now();
    const pending = downloadProcessingResult(JOB_ID);
    const outcome = expect(pending).rejects.toThrow("Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(60_000);
    expect(Date.now() - startedAt).toBeLessThan(90_000);
  });

  it("gives up on the download after about a minute offline, and at once for an expired result", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const startedAt = Date.now();
    const offline = downloadProcessingResult(JOB_ID);
    const outcome = expect(offline).rejects.toThrow("Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(60_000);
    expect(Date.now() - startedAt).toBeLessThan(90_000);

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "not_found", message: "Auftrag wurde nicht gefunden oder ist abgelaufen." } }, 404),
    );
    await expect(downloadProcessingResult(JOB_ID)).rejects.toThrow("Auftrag wurde nicht gefunden oder ist abgelaufen.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses non-JPEG result bodies", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    await expect(downloadProcessingResult(JOB_ID)).rejects.toThrow("Das bearbeitete Foto konnte nicht geladen werden.");
  });

  it("shows the processor's German message when it refuses a style it does not implement", async () => {
    // The processor answers POST /jobs/upload …
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "preset_unavailable", message: PRESET_UNAVAILABLE_MESSAGE } }, 400),
    );
    const processorError = await new RealImageProcessor({ baseUrl: "http://processor:8000", apiKey: "key" })
      .submitUpload({ file, fileName: "front.jpg", preset: "autoexperten_dark", shotKey: "front" })
      .catch((reason: unknown) => reason);
    // … the app route passes it on, and the browser client turns it into the photo's message.
    fetchMock.mockResolvedValueOnce(processingErrorResponse(processorError));

    const error = await submitUploadProcessing({ file, preset: "autoexperten_dark", shotKey: "front" }).catch(
      (reason: unknown) => reason,
    );
    expect(toUserMessage(error)).toBe(PRESET_UNAVAILABLE_MESSAGE);
    expect(fetchMock).toHaveBeenCalledTimes(2); // not retried
  });
});

describe("waitForProcessingJob", () => {
  const JOB_ID = "0123456789abcdef0123456789abcdef";

  function job(status: ProcessingJobStatus) {
    return {
      jobId: JOB_ID,
      vehicleId: null,
      photoId: null,
      preset: "autoexperten_standard",
      status,
      progress: status === "complete" ? 1 : 0.4,
      createdAt: "",
      updatedAt: "",
      result: status === "complete" ? { kind: "file", width: 3200, height: 2400, bytes: 900_000 } : null,
      error: null,
      errorCode: null,
      warnings: [],
    };
  }

  const answer = (status: ProcessingJobStatus) => jsonResponse(job(status), 200);
  const gatewayError = (status: number, code: string) =>
    jsonResponse({ error: { code, message: "Die Bildbearbeitung ist derzeit nicht erreichbar." } }, status);

  it("keeps polling through a dropped connection and gateway errors until the job is done", async () => {
    fetchMock
      .mockResolvedValueOnce(answer("processing"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(gatewayError(502, "processing_unavailable"))
      .mockResolvedValueOnce(new Response("<html>Service Unavailable</html>", { status: 503 }))
      .mockResolvedValueOnce(gatewayError(504, "processing_timeout"))
      .mockResolvedValueOnce(answer("complete"));
    const seen: string[] = [];

    const pending = waitForProcessingJob(JOB_ID, { onUpdate: (update) => seen.push(update.status) });
    await vi.runAllTimersAsync();

    await expect(pending).resolves.toMatchObject({ status: "complete" });
    expect(fetchMock).toHaveBeenCalledTimes(6);
    expect(seen).toEqual(["processing", "complete"]);
  });

  it.each([
    [401, "unauthorized", "Bitte melden Sie sich erneut an."],
    [404, "not_found", "Auftrag wurde nicht gefunden oder ist abgelaufen."],
  ])("stops at once on a %i answer", async (status, code, message) => {
    fetchMock
      .mockResolvedValueOnce(answer("processing"))
      .mockResolvedValueOnce(jsonResponse({ error: { code, message } }, status));

    const pending = waitForProcessingJob(JOB_ID);
    const outcome = expect(pending).rejects.toThrow(message);
    await vi.runAllTimersAsync();
    await outcome;
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("gives up with the connection message when polls fail for longer than the job timeout", async () => {
    fetchMock.mockResolvedValueOnce(answer("processing")).mockRejectedValue(new TypeError("Failed to fetch"));
    const startedAt = Date.now();

    const pending = waitForProcessingJob(JOB_ID);
    const outcome = expect(pending).rejects.toThrow("Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(Date.now() - startedAt).toBeGreaterThan(5 * 60_000);
    expect(Date.now() - startedAt).toBeLessThan(6 * 60_000);
  });

  it("does not count the wait in the processor queue against the processing timeout", async () => {
    // 20 minutes behind other photos, then 2 minutes of processing.
    let polls = 0;
    fetchMock.mockImplementation(async () => {
      polls += 1;
      return answer(polls <= 120 ? "queued" : polls <= 132 ? "processing" : "complete");
    });

    const pending = waitForProcessingJob(JOB_ID, { intervalMs: 10_000 });
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toMatchObject({ status: "complete" });
  });

  it("still ends jobs that take too long once they are processed", async () => {
    fetchMock.mockImplementation(async () => answer("processing"));
    const startedAt = Date.now();

    const pending = waitForProcessingJob(JOB_ID, { intervalMs: 10_000 });
    const outcome = expect(pending).rejects.toThrow("Die Bearbeitung dauert ungewöhnlich lange.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(Date.now() - startedAt).toBeLessThan(6 * 60_000);
  });
});
