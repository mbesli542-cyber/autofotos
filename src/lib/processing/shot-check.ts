/**
 * Check state of a shot as the camera, "Fotos überprüfen" and the vehicle
 * page show it (pure) – from the shot's slot (saved photo + pending capture)
 * and the automatic checks (photo-checks.ts):
 *
 * - none:     nothing to show (not captured, not checked, extra photo, checks off)
 * - checking: the shot's newest capture is being saved or checked
 * - ok:       its result is saved (`hint`: a processor hint asking to look at it)
 * - saved:    an interior or detail shot – kept in its original environment
 *             and never checked by the processor: neutral, never "passt"
 * - retake:   the processor rejected it or its result says the photo is the
 *             problem (`reason`: the processor's German text) – also on
 *             another device: a rejection is kept with the photo
 * - error:    it could not be checked (`reason`); "Fotos bearbeiten" processes it later
 *
 * A check always belongs to one photo: the result of a photo that a newer
 * capture replaced is never shown for the shot – also not while the photo
 * list on screen still shows the replaced photo (`captures`).
 *
 * The German wording of each state is the same on every screen
 * (`shotCheckLabel`).
 */
import type { ProcessingWarning } from "@/lib/domain/types";
import { isOlderCapture, isOlderThanSavedPhoto } from "@/lib/photos/capture-order";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import type { ShotCategory } from "@/lib/shots/shot-template";
import type { PhotoCheck, PhotoChecksSnapshot, SavedCapture } from "./photo-checks";
import { PROCESSOR_UNREACHABLE_SENTENCE } from "./processor-connection";
import { getWarningKind, storedRejection, warningsToCheck } from "./processing-warnings";
import { getCategoryTreatment } from "./shot-treatment";
import { PROCESSING_NOT_CONNECTED_MESSAGE } from "./types";

export type ShotCheckState =
  | { kind: "none" }
  | { kind: "checking" }
  | { kind: "ok"; hint: string | null }
  | { kind: "saved" }
  | { kind: "retake"; reason: string }
  | { kind: "error"; reason: string; accessCode?: boolean };

/** errorCode of a check refused because the stored access code is wrong (demo mode). */
export const ACCESS_CODE_INVALID_CODE = "access_code_invalid";

const NONE: ShotCheckState = { kind: "none" };
const CHECKING: ShotCheckState = { kind: "checking" };
const SAVED: ShotCheckState = { kind: "saved" };

/** One wording per state on every screen (camera, "Fotos überprüfen", large view, vehicle page). */
export const SHOT_CHECK_LABELS = {
  checking: "Wird geprüft…",
  ok: "Geprüft – passt",
  hint: "Geprüft – bitte Ergebnis ansehen",
  saved: "Gespeichert",
  retake: "Bitte neu aufnehmen",
  error: "Prüfung nicht möglich",
} as const;

/** The short German label of a state (null: nothing to show). */
export function shotCheckLabel(state: ShotCheckState): string | null {
  switch (state.kind) {
    case "none":
      return null;
    case "ok":
      return state.hint ? SHOT_CHECK_LABELS.hint : SHOT_CHECK_LABELS.ok;
    default:
      return SHOT_CHECK_LABELS[state.kind];
  }
}

/** The text that goes with the label: the processor's hint or reason (null: none). */
export function shotCheckDetail(state: ShotCheckState): string | null {
  switch (state.kind) {
    case "ok":
      return state.hint;
    case "retake":
    case "error":
      return state.reason;
    default:
      return null;
  }
}

/**
 * Label and detail as one text, e.g. "Bitte neu aufnehmen: <Grund>" or
 * "Geprüft – passt" (null: nothing to show) – the same on every screen.
 */
export function shotCheckDescription(state: ShotCheckState): string | null {
  const label = shotCheckLabel(state);
  if (label === null) return null;
  const detail = shotCheckDetail(state);
  return detail ? `${label}: ${detail}` : label;
}

/**
 * Summary of results the processor asks to look at, e.g. "1 Foto: Geprüft –
 * bitte Ergebnis ansehen (Hinweis in der Großansicht)." – `where`: where the
 * hints are shown.
 */
export function resultsToCheckMessage(count: number, where: "viewer" | "list"): string {
  const photos = count === 1 ? "1 Foto" : `${count} Fotos`;
  const hints = count === 1 ? "Hinweis" : "Hinweise";
  return `${photos}: ${SHOT_CHECK_LABELS.hint} (${hints} ${where === "viewer" ? "in der Großansicht" : "in der Liste"}).`;
}

/**
 * Interior and detail shots are passed through, never checked (pure): a
 * captured one is just "saved" – only a refusal of the file itself (e.g. too
 * large) asks for a retake.
 */
export function passThroughState(state: ShotCheckState, captured: boolean): ShotCheckState {
  if (state.kind === "retake") return state;
  return captured ? SAVED : NONE;
}

/** Whether shots of `category` are only passed through by the processor (interior, detail). */
export function isPassThroughCategory(category: ShotCategory | null): boolean {
  return category !== null && getCategoryTreatment(category) === "original_environment";
}

/** Shown when the processor gave no text (never expected). */
const RETAKE_FALLBACK = "Bitte Foto neu aufnehmen.";

function fromWarnings(warnings: readonly ProcessingWarning[]): ShotCheckState {
  const relevant = warningsToCheck(warnings);
  const retake = relevant.find((warning) => getWarningKind(warning.code) === "retake");
  if (retake) return { kind: "retake", reason: retake.message };
  return { kind: "ok", hint: relevant[0]?.message ?? null };
}

/** The state a check of a photo stands for (`checksOn`: a waiting check counts as being checked). */
export function getPhotoCheckState(check: PhotoCheck, checksOn = true): ShotCheckState {
  switch (check.status) {
    case "waiting":
      return checksOn ? CHECKING : NONE;
    case "checking":
      return CHECKING;
    case "ok":
      return fromWarnings(check.warnings);
    case "retake":
      return { kind: "retake", reason: check.message ?? RETAKE_FALLBACK };
    case "error":
      return check.errorCode === ACCESS_CODE_INVALID_CODE
        ? { kind: "error", reason: check.message ?? "Der Zugangscode ist falsch.", accessCode: true }
        : { kind: "error", reason: check.message ?? "Automatische Prüfung nicht möglich." };
  }
}

/**
 * The check of the shot's current photo: the saved photo's own check, or the
 * check of a newer capture that the photo list does not show yet (it was
 * saved a moment ago).
 */
function currentCheck(slot: Pick<PhotoSlot, "key" | "photo">, checks: readonly PhotoCheck[]): PhotoCheck | null {
  const { photo } = slot;
  const counts = (check: PhotoCheck): boolean => {
    if (check.shotKey !== slot.key) return false;
    if (photo) return check.photoId === photo.id || (check.takenAt !== "" && !isOlderThanSavedPhoto(check.takenAt, photo));
    // No saved photo (e.g. deleted): only a capture being checked right now counts.
    return check.status === "waiting" || check.status === "checking";
  };
  let best: PhotoCheck | null = null;
  for (const check of checks.filter(counts)) {
    const newer = best === null || isOlderCapture(best.takenAt, check.takenAt);
    const tie = best !== null && !isOlderCapture(check.takenAt, best.takenAt) && check.photoId === photo?.id;
    if (newer || tie) best = check;
  }
  return best;
}

/**
 * `newestCapture`: the newest capture of the shot saved in this tab – a saved
 * photo older than it was replaced, its result no longer counts.
 */
export function getShotCheckState(
  slot: Pick<PhotoSlot, "key" | "category" | "photo" | "pending">,
  vehicleChecks: readonly PhotoCheck[],
  availability: PhotoChecksSnapshot["availability"],
  newestCapture?: SavedCapture,
): ShotCheckState {
  // Extra photos are never processed.
  if (slot.category === null) return NONE;
  const state = checkedShotState(slot, vehicleChecks, availability, newestCapture);
  if (!isPassThroughCategory(slot.category)) return state;
  // Still being saved: not "saved" yet.
  return passThroughState(state, slot.photo !== null && !slot.pending);
}

function checkedShotState(
  slot: Pick<PhotoSlot, "key" | "category" | "photo" | "pending">,
  vehicleChecks: readonly PhotoCheck[],
  availability: PhotoChecksSnapshot["availability"],
  newestCapture?: SavedCapture,
): ShotCheckState {
  const checksOn = availability === "on";
  // A newer capture is still being saved: it is checked next – the photo it replaces no longer counts.
  if (slot.pending) return slot.pending.status !== "failed" && checksOn ? CHECKING : NONE;
  const check = currentCheck(slot, vehicleChecks);
  // What is saved with the photo: its result (with hints) or the processor's rejection.
  const stored = slot.photo !== null && (slot.photo.processedStoragePath !== null || storedRejection(slot.photo) !== null);
  if (check) {
    const state = getPhotoCheckState(check, checksOn);
    // A check that could not run: what was saved earlier for the photo still tells.
    if (state.kind !== "error" || !stored || check.photoId !== slot.photo?.id) return state;
  }
  const replaced =
    slot.photo !== null &&
    newestCapture !== undefined &&
    newestCapture.photoId !== slot.photo.id &&
    !isOlderThanSavedPhoto(newestCapture.takenAt, slot.photo);
  if (slot.photo && stored && !replaced) return fromWarnings(slot.photo.processingWarnings);
  return NONE;
}

/** The check states of the slots by shot key (only for the vehicle's checks). */
export function getShotCheckStates(
  slots: readonly Pick<PhotoSlot, "key" | "category" | "photo" | "pending">[],
  vehicleId: string,
  snapshot: PhotoChecksSnapshot,
): Map<string, ShotCheckState> {
  const checks = [...snapshot.checks.values()].filter((check) => check.vehicleId === vehicleId);
  return new Map(
    slots.map((slot) => [
      slot.key,
      getShotCheckState(slot, checks, snapshot.availability, snapshot.captures.get(`${vehicleId}/${slot.key}`)),
    ]),
  );
}

/** "01".."15" (a shot of the template). */
function shotNumber(order: number): string {
  return String(order).padStart(2, "0");
}

/** A message in the camera's check status line: a bold label and more text after a colon. */
export interface CheckMessage {
  label: string;
  detail: string | null;
}

/**
 * Message when a check of a shot finished (camera), with the state's one
 * wording, e.g. "02 Vorne · Geprüft – passt" or "01 Vorne links (45°) ·
 * Bitte neu aufnehmen" + the reason. null: nothing to report.
 */
export function checkResultMessage(shot: { order: number; title: string }, state: ShotCheckState): CheckMessage | null {
  const name = `${shotNumber(shot.order)} ${shot.title}`;
  switch (state.kind) {
    case "ok":
    case "retake":
      return { label: `${name} · ${shotCheckLabel(state)}`, detail: shotCheckDetail(state) };
    case "error":
      return {
        label: `${name} · ${SHOT_CHECK_LABELS.error}`,
        detail: state.accessCode
          ? "Der Zugangscode ist falsch – bitte neu eingeben."
          : "Wird unter „Fotos bearbeiten“ nachgeholt.",
      };
    default:
      return null;
  }
}

/**
 * Whether the capture of a shot taken at `takenAt` – or a newer one – was
 * already rejected by its check (pure). A rejection of the photo that capture
 * replaced does not count: that shot is being checked again.
 */
export function isCaptureRejected(
  checks: Iterable<PhotoCheck>,
  capture: { vehicleId: string; shotKey: string; takenAt: string },
): boolean {
  for (const check of checks) {
    if (check.vehicleId !== capture.vehicleId || check.shotKey !== capture.shotKey || check.status !== "retake") continue;
    if (check.takenAt !== "" && !isOlderCapture(check.takenAt, capture.takenAt)) return true;
  }
  return false;
}

/** "Noch 1 Foto neu aufnehmen" / "Noch 3 Fotos neu aufnehmen" (camera, after a retake). */
export function retakesLeftMessage(count: number): string {
  return `Noch ${count} ${count === 1 ? "Foto" : "Fotos"} neu aufnehmen`;
}

/** Why the automatic check is off (German, for the camera's hint); null when it is on or unknown. */
export function autoCheckOffMessage(availability: PhotoChecksSnapshot["availability"]): string | null {
  switch (availability) {
    case "no_processor":
      return `${PROCESSING_NOT_CONNECTED_MESSAGE} Die Kamera funktioniert wie gewohnt – die Fotos werden nicht automatisch geprüft.`;
    case "unreachable":
      return `${PROCESSOR_UNREACHABLE_SENTENCE} Die Kamera funktioniert wie gewohnt – die Fotos werden geprüft, sobald er wieder erreichbar ist, spätestens unter „Fotos bearbeiten“.`;
    case "offline":
      return "Keine Internetverbindung. Die Fotos werden auf dem Gerät gesichert und geprüft, sobald die Verbindung wieder da ist.";
    case "access_code":
      return "Für die Prüfung wird der Zugangscode der Bildbearbeitung benötigt. Geben Sie ihn einmal hier oder unter „Fotos bearbeiten“ ein – er wird nur auf diesem Gerät gespeichert.";
    default:
      return null;
  }
}
