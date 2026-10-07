/** Opening the rear camera with getUserMedia and mapping errors to German. */
import { CAMERA_CONFIG } from "./config";

export type CameraErrorCode =
  | "permission_denied"
  | "not_found"
  | "in_use"
  | "insecure_context"
  | "not_supported"
  | "unknown";

export const CAMERA_ERROR_MESSAGES: Record<CameraErrorCode, { title: string; description: string }> = {
  permission_denied: {
    title: "Kamera konnte nicht geöffnet werden.",
    description:
      "Bitte erlauben Sie den Kamerazugriff in den Browser-Einstellungen und versuchen Sie es erneut.",
  },
  not_found: {
    title: "Keine Kamera gefunden.",
    description: "Sie können stattdessen ein Foto hochladen.",
  },
  in_use: {
    title: "Die Kamera wird bereits verwendet.",
    description: "Bitte schließen Sie andere Apps, die die Kamera nutzen, und versuchen Sie es erneut.",
  },
  insecure_context: {
    title: "Kamera nur über HTTPS verfügbar.",
    description: "Öffnen Sie die App über eine sichere Verbindung (https://) oder laden Sie ein Foto hoch.",
  },
  not_supported: {
    title: "Kamera wird von diesem Browser nicht unterstützt.",
    description: "Bitte laden Sie stattdessen ein Foto hoch.",
  },
  unknown: {
    title: "Kamera konnte nicht geöffnet werden.",
    description: "Bitte versuchen Sie es erneut.",
  },
};

export class CameraError extends Error {
  constructor(readonly code: CameraErrorCode, cause?: unknown) {
    super(CAMERA_ERROR_MESSAGES[code].title, { cause });
    this.name = "CameraError";
  }
}

export function mapCameraError(error: unknown): CameraErrorCode {
  if (error instanceof CameraError) return error.code;
  const name = error && typeof error === "object" && "name" in error ? String(error.name) : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
    case "PermissionDeniedError":
      return "permission_denied";
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return "not_found";
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return "in_use";
    default:
      return "unknown";
  }
}

/** Rear camera, highest reasonable resolution, no audio. */
export async function openCameraStream(): Promise<MediaStream> {
  if (typeof window !== "undefined" && !window.isSecureContext) {
    throw new CameraError("insecure_context");
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new CameraError("not_supported");
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: CAMERA_CONFIG.idealWidth },
        height: { ideal: CAMERA_CONFIG.idealHeight },
      },
    });
  } catch (error) {
    if (mapCameraError(error) !== "not_found") throw error;
    // Retry without resolution hints for unusual devices.
    return navigator.mediaDevices.getUserMedia({ audio: false, video: true });
  }
}

export function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}
