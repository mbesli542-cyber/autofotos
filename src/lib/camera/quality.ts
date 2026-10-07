/**
 * Capture quality checks – INTERFACE ONLY for now.
 *
 * The MVP shows the framing overlay only. Later, a checker (on-device model
 * or server API) can implement `CaptureQualityChecker` to warn about
 * distance, centering, angle, blur, darkness or cropping. The camera already
 * calls the checker after each capture and displays returned warnings.
 */
import type { ShotDefinition } from "@/lib/shots/shot-template";

export type CaptureQualityWarningCode =
  | "too_close"
  | "too_far"
  | "not_centered"
  | "wrong_angle"
  | "blurry"
  | "too_dark"
  | "vehicle_cropped";

export const CAPTURE_QUALITY_MESSAGES: Record<CaptureQualityWarningCode, string> = {
  too_close: "Das Fahrzeug ist zu nah. Bitte etwas zurückgehen.",
  too_far: "Das Fahrzeug ist zu weit entfernt. Bitte näher herangehen.",
  not_centered: "Das Fahrzeug ist nicht mittig. Bitte an der Vorlage ausrichten.",
  wrong_angle: "Der Aufnahmewinkel passt nicht zur Vorlage.",
  blurry: "Das Foto ist unscharf. Bitte ruhig halten und erneut aufnehmen.",
  too_dark: "Das Foto ist zu dunkel. Bitte für mehr Licht sorgen.",
  vehicle_cropped: "Das Fahrzeug ist angeschnitten. Bitte das ganze Fahrzeug erfassen.",
};

export interface CaptureQualityWarning {
  code: CaptureQualityWarningCode;
  message: string;
}

export interface CaptureQualityResult {
  /** null = not evaluated. */
  isCentered: boolean | null;
  isSharp: boolean | null;
  isBrightEnough: boolean | null;
  /** 0..1, 1 = matches the template angle. */
  angleScore: number | null;
  /** 0..1, 1 = ideal distance. */
  distanceScore: number | null;
  warnings: CaptureQualityWarning[];
}

export interface CaptureQualityChecker {
  analyzeCapture(image: Blob, shot: ShotDefinition): Promise<CaptureQualityResult>;
}

export const NOT_EVALUATED: CaptureQualityResult = {
  isCentered: null,
  isSharp: null,
  isBrightEnough: null,
  angleScore: null,
  distanceScore: null,
  warnings: [],
};

/** MVP checker: performs no analysis. */
export const noopQualityChecker: CaptureQualityChecker = {
  async analyzeCapture() {
    return NOT_EVALUATED;
  },
};
