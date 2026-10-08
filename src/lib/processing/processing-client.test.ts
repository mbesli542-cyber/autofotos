import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppError } from "@/lib/errors";
import {
  AccessCodeRequiredError,
  ProcessingNotConnectedError,
  downloadProcessingResult,
  submitPhotoProcessing,
  submitUploadProcessing,
} from "./processing-client";
import { PROCESSING_BUSY_CODE } from "./types";

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

  it("gives up after the retries with the German busy message", async () => {
    fetchMock.mockImplementation(async () => busy());

    const pending = submitPhotoProcessing(REQUEST);
    const outcome = expect(pending).rejects.toThrow("Die Bildbearbeitung ist gerade ausgelastet.");
    await vi.runAllTimersAsync();
    await outcome;
    expect(fetchMock).toHaveBeenCalledTimes(5);
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

  it("refuses non-JPEG result bodies", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 200, headers: { "Content-Type": "text/html" } }));
    await expect(downloadProcessingResult(JOB_ID)).rejects.toThrow("Das bearbeitete Foto konnte nicht geladen werden.");
  });
});
