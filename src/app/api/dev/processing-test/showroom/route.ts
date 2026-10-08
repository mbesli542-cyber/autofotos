/**
 * GET /api/dev/processing-test/showroom[?preset=autoexperten_standard&width=1600]
 * → streams processor GET /showroom/{preset}.jpg?width=N (image/jpeg): the
 * branded showroom background WITHOUT a vehicle, for the comparison on the
 * developer test page when no debug background.jpg exists.
 * Passes the processor header X-Showroom-Source (master | fallback) through.
 */
import { connection, type NextRequest } from "next/server";
import {
  PROCESSOR_TIMEOUTS_MS,
  apiError,
  callProcessor,
  guardDevProcessorRoute,
  passThroughLength,
  processorErrorResponse,
} from "@/lib/dev-tools";
import {
  DEFAULT_DEV_TEST_PRESET,
  DEV_SHOWROOM_WIDTH,
  SHOWROOM_SOURCE_HEADER,
  isDevTestPresetId,
  parseShowroomSource,
  parseShowroomWidth,
} from "@/lib/processing/dev-test-types";

export async function GET(request: NextRequest) {
  await connection(); // runtime env switch – never prerender
  const guard = guardDevProcessorRoute();
  if (!guard.ok) return guard.response;

  const { searchParams } = request.nextUrl;
  const preset = searchParams.get("preset") ?? DEFAULT_DEV_TEST_PRESET;
  if (!isDevTestPresetId(preset)) {
    return apiError(400, "invalid_preset", "Unbekannter Bearbeitungsstil.");
  }
  const width = parseShowroomWidth(searchParams.get("width"));
  if (width === null) {
    return apiError(
      400,
      "invalid_width",
      `Ungültige Bildbreite (erlaubt: ${DEV_SHOWROOM_WIDTH.min} bis ${DEV_SHOWROOM_WIDTH.max} Pixel).`,
    );
  }

  const call = await callProcessor(guard.value, `/showroom/${encodeURIComponent(preset)}.jpg`, {
    method: "GET",
    query: { width: String(width) },
    accept: "image/jpeg",
    timeoutMs: PROCESSOR_TIMEOUTS_MS.file,
    signal: request.signal,
  });
  if (!call.ok) return call.response;
  const upstream = call.value;
  if (!upstream.ok || !upstream.body) {
    return processorErrorResponse(upstream, {
      notFound: "Der Showroom für diesen Bearbeitungsstil ist nicht verfügbar.",
    });
  }

  const headers = new Headers({
    "Content-Type": "image/jpeg",
    "Cache-Control": "no-store",
    "Content-Disposition": `inline; filename="AE_showroom_${preset}_${width}.jpg"`,
  });
  const length = passThroughLength(upstream);
  if (length) headers.set("Content-Length", length);
  // Only the two known values – never reflect arbitrary upstream header content.
  const source = parseShowroomSource(upstream.headers.get(SHOWROOM_SOURCE_HEADER)?.trim().toLowerCase());
  if (source) headers.set(SHOWROOM_SOURCE_HEADER, source);

  return new Response(upstream.body, { status: 200, headers });
}
