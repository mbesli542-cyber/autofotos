/**
 * How "Fotos bearbeiten" describes the showroom processing (pure). Daily use
 * needs to tell apart a phone that is just offline or a dealer PC that is
 * asleep from a deployment that has no processing at all – only the last one
 * says "noch nicht verbunden". Texts avoid technical words ("Prozessor").
 */
import type { ProcessingStatus } from "./types";
import { PROCESSING_NOT_CONNECTED_MESSAGE } from "./types";

/**
 * - checking:       the status is being asked
 * - connected:      a processor answers (the showroom may still be incomplete)
 * - offline:        this device has no internet connection
 * - unreachable:    a processor is configured but does not answer (PC asleep, restarting, …)
 * - misconfigured:  it answers but cannot be used (connectionError, e.g. no storage access)
 * - not_configured: this deployment has no processor at all
 */
export type ProcessorConnectionState =
  | "checking"
  | "connected"
  | "offline"
  | "unreachable"
  | "misconfigured"
  | "not_configured";

export const PROCESSING_OFFLINE_MESSAGE =
  "Keine Internetverbindung – die Bearbeitung ist wieder möglich, sobald Sie online sind.";
/** First sentence of the "unreachable" texts (also the camera's "Prüfung aus" hint). */
export const PROCESSOR_UNREACHABLE_SENTENCE = "Der Showroom-Rechner ist gerade nicht erreichbar.";
export const PROCESSOR_UNREACHABLE_MESSAGE = `${PROCESSOR_UNREACHABLE_SENTENCE} Bitte später erneut versuchen.`;

export interface ProcessorConnectionInput {
  phase: "loading" | "ready" | "error";
  status: ProcessingStatus | null;
  /** navigator.onLine (true when unknown). */
  online: boolean;
  /** NEXT_PUBLIC_IMAGE_PROCESSOR – only for the first answer (client-config.ts). */
  uiHint: "real" | "mock";
}

export function getProcessorConnection({ phase, status, online, uiHint }: ProcessorConnectionInput): ProcessorConnectionState {
  if (!online) return "offline";
  if (phase === "loading") return uiHint === "real" ? "checking" : "not_configured";
  // No answer at all (weak connection, server restarting): try again later – never "not connected".
  if (phase === "error" || !status) return "unreachable";
  if (status.connected) return "connected";
  if (status.processor !== "real") return "not_configured";
  return status.connectionError ? "misconfigured" : "unreachable";
}

export interface ProcessorConnectionText {
  /** Short chip label. */
  label: string;
  /** Main message (null: nothing to explain). */
  message: string | null;
  /** Second line under the message. */
  detail: string | null;
  /** Asking again can help ("Erneut prüfen"). */
  canRetry: boolean;
}

const ORIGINALS_KEPT = "Die Originalfotos bleiben unverändert.";

/** German texts of a connection state (`connectionError`: the status's own message). */
export function describeProcessorConnection(
  state: ProcessorConnectionState,
  connectionError: string | null = null,
): ProcessorConnectionText {
  switch (state) {
    case "checking":
      return { label: "Bildbearbeitung wird geprüft…", message: null, detail: null, canRetry: false };
    case "connected":
      return { label: "Reale Bildbearbeitung", message: null, detail: null, canRetry: false };
    case "offline":
      return {
        label: "Keine Internetverbindung",
        message: PROCESSING_OFFLINE_MESSAGE,
        detail: `Aufnehmen und Speichern funktioniert weiter. ${ORIGINALS_KEPT}`,
        canRetry: true,
      };
    case "unreachable":
      return {
        label: "Showroom-Rechner nicht erreichbar",
        message: PROCESSOR_UNREACHABLE_MESSAGE,
        detail: `Bis dahin werden keine Fotos bearbeitet. ${ORIGINALS_KEPT}`,
        canRetry: true,
      };
    case "misconfigured":
      return {
        label: "Bildbearbeitung nicht verfügbar",
        message: connectionError ?? PROCESSOR_UNREACHABLE_MESSAGE,
        detail: `Es werden keine Fotos bearbeitet. ${ORIGINALS_KEPT}`,
        canRetry: true,
      };
    case "not_configured":
      return {
        label: "Showroom-Bearbeitung nicht verbunden",
        message: PROCESSING_NOT_CONNECTED_MESSAGE,
        detail: `Es werden keine Fotos bearbeitet oder gespeichert, bis die AutoExperten Showroom-Bearbeitung angebunden ist. ${ORIGINALS_KEPT}`,
        canRetry: false,
      };
  }
}
