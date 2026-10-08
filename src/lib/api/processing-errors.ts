/**
 * Maps processor failures to German API errors (server only).
 * The processor's own German messages are kept where they help the
 * employee; technical details (URLs, keys, stack traces) never leave the server.
 */
import { NextResponse } from "next/server";
import { ProcessingServiceError } from "@/lib/processing/real-image-processor";
import {
  PROCESSING_BUSY_CODE,
  PROCESSING_NOT_CONNECTED_CODE,
  PROCESSING_NOT_CONNECTED_MESSAGE,
} from "@/lib/processing/types";
import { apiError } from "./route-helpers";

export function processingNotConnected(): NextResponse {
  return apiError(503, PROCESSING_NOT_CONNECTED_CODE, PROCESSING_NOT_CONNECTED_MESSAGE);
}

export const PROCESSING_UNREACHABLE_MESSAGE =
  "Die Bildbearbeitung ist derzeit nicht erreichbar. Bitte versuchen Sie es später erneut.";

export function processingErrorResponse(
  error: unknown,
  messages: { notFound?: string; conflict?: string } = {},
): NextResponse {
  if (!(error instanceof ProcessingServiceError)) {
    return apiError(502, "processing_unavailable", PROCESSING_UNREACHABLE_MESSAGE);
  }
  if (error.reason === "timeout") {
    return apiError(504, "processing_timeout", "Die Bildbearbeitung antwortet nicht. Bitte versuchen Sie es erneut.");
  }
  if (error.reason === "network") {
    return apiError(502, "processing_unavailable", PROCESSING_UNREACHABLE_MESSAGE);
  }
  if (error.reason === "invalid_response") {
    return apiError(502, "processing_invalid_response", "Ungültige Antwort der Bildbearbeitung. Bitte versuchen Sie es erneut.");
  }

  const { status, code, processorMessage } = error;
  if (status === 401 || status === 403) {
    console.warn("[processing] The processor rejected the API key (check IMAGE_PROCESSING_API_KEY).");
    return apiError(
      502,
      "processing_rejected",
      "Die Bildbearbeitung hat die Anfrage abgelehnt. Bitte informieren Sie den Administrator.",
    );
  }
  if (status === 404) {
    return apiError(404, "not_found", messages.notFound ?? "Auftrag wurde nicht gefunden oder ist abgelaufen.");
  }
  if (status === 409) {
    return apiError(409, "not_ready", messages.conflict ?? "Das Ergebnis ist noch nicht fertig.");
  }
  if (status === 413) {
    return apiError(413, "file_too_large", processorMessage ?? "Das Foto ist zu groß für die Bildbearbeitung.");
  }
  if (status === 400 || status === 415 || status === 422) {
    return apiError(
      400,
      "invalid_request",
      processorMessage ?? "Das Foto konnte nicht verarbeitet werden. Bitte prüfen Sie das Dateiformat.",
    );
  }
  if (status === 429 || (status === 503 && (code === "busy" || code === null))) {
    // Queue full – the client retries after a short wait.
    return apiError(
      503,
      PROCESSING_BUSY_CODE,
      "Die Bildbearbeitung ist gerade ausgelastet. Bitte in Kürze erneut versuchen.",
    );
  }
  if (status === 503) {
    // Configuration problems (e.g. showroom, storage) keep the processor's German text.
    return apiError(
      503,
      code === "configuration" ? "processing_configuration" : "processing_unavailable",
      processorMessage ?? "Die Bildbearbeitung ist derzeit nicht verfügbar. Bitte versuchen Sie es später erneut.",
    );
  }
  return apiError(
    502,
    "processing_error",
    "Die Bildbearbeitung hat einen Fehler gemeldet. Bitte versuchen Sie es erneut.",
  );
}
