/**
 * Developer proxy to the image processor (only with dev tools enabled).
 *
 * GET  /api/dev/processing-test → processor GET /health
 * POST /api/dev/processing-test  multipart: file, preset, shotKey
 *      → processor POST /jobs/upload → 202 ProcessorJob
 *
 * The browser never sees IMAGE_PROCESSING_API_URL / _KEY.
 */
import { NextResponse, connection, type NextRequest } from "next/server";
import {
  PROCESSOR_TIMEOUTS_MS,
  apiError,
  callProcessor,
  guardDevProcessorRoute,
  invalidProcessorResponse,
  processorErrorResponse,
  readJson,
} from "@/lib/dev-tools";
import {
  DEFAULT_DEV_TEST_PRESET,
  DEFAULT_DEV_TEST_SHOT_KEY,
  DEV_TEST_MAX_UPLOAD_BYTES,
  DEV_TEST_MAX_UPLOAD_MB,
  isDevTestPresetId,
  parseProcessorHealth,
  parseProcessorJob,
  type ProcessorHealth,
  type ProcessorJob,
} from "@/lib/processing/dev-test-types";
import { DEFAULT_SHOT_TEMPLATE, getShot } from "@/lib/shots/shot-template";

/** Multipart overhead allowance on top of the file size limit. */
const MULTIPART_OVERHEAD_BYTES = 64 * 1024;

export async function GET(request: NextRequest) {
  await connection(); // runtime env switch – never prerender
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const call = await callProcessor(guard.value, "/health", {
    method: "GET",
    accept: "application/json",
    timeoutMs: PROCESSOR_TIMEOUTS_MS.health,
    signal: request.signal,
  });
  if (!call.ok) return call.response;
  if (!call.value.ok) {
    return processorErrorResponse(call.value, {
      notFound: "Der Bildverarbeitungs-Service bietet keinen Status an (/health).",
    });
  }
  const health = parseProcessorHealth(await readJson(call.value));
  if (!health) return invalidProcessorResponse();
  return NextResponse.json<ProcessorHealth>(health, { headers: { "Cache-Control": "no-store" } });
}

function formString(form: FormData, key: string): string | null {
  const value = form.get(key);
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export async function POST(request: NextRequest) {
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const tooLarge = () =>
    apiError(413, "file_too_large", `Das Foto ist zu groß (maximal ${DEV_TEST_MAX_UPLOAD_MB} MB).`);

  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > DEV_TEST_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES) {
    return tooLarge();
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return apiError(400, "invalid_request", "Ungültige Anfrage. Bitte wählen Sie ein Foto aus.");
  }

  const file = form.get("file");
  if (!(file instanceof Blob) || file.size === 0) {
    return apiError(400, "missing_file", "Bitte wählen Sie ein Fahrzeugfoto aus.");
  }
  if (file.size > DEV_TEST_MAX_UPLOAD_BYTES) return tooLarge();
  if (!file.type.toLowerCase().startsWith("image/")) {
    return apiError(415, "unsupported_type", "Nur Bilddateien (z. B. JPG, PNG, HEIC) werden unterstützt.");
  }

  const preset = formString(form, "preset") ?? DEFAULT_DEV_TEST_PRESET;
  if (!isDevTestPresetId(preset)) {
    return apiError(400, "invalid_preset", "Unbekannter Bearbeitungsstil.");
  }
  const shotKey = formString(form, "shotKey") ?? DEFAULT_DEV_TEST_SHOT_KEY;
  if (!getShot(DEFAULT_SHOT_TEMPLATE, shotKey)) {
    return apiError(400, "invalid_shot", "Unbekannte Aufnahmeposition.");
  }

  // Forward only the three known fields – nothing else from the request.
  const upstream = new FormData();
  const fileName = file instanceof File && file.name ? file.name : "upload.jpg";
  upstream.append("file", file, fileName);
  upstream.append("preset", preset);
  upstream.append("shotKey", shotKey);

  const call = await callProcessor(guard.value, "/jobs/upload", {
    method: "POST",
    body: upstream,
    accept: "application/json",
    timeoutMs: PROCESSOR_TIMEOUTS_MS.upload,
    signal: request.signal,
  });
  if (!call.ok) return call.response;
  if (!call.value.ok) {
    return processorErrorResponse(call.value, {
      notFound: "Der Bildverarbeitungs-Service unterstützt keine Uploads (/jobs/upload).",
    });
  }

  const job = parseProcessorJob(await readJson(call.value));
  if (!job) return invalidProcessorResponse();
  return NextResponse.json<ProcessorJob>(job, {
    status: 202,
    headers: { "Cache-Control": "no-store" },
  });
}
