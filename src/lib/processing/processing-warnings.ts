/**
 * Hints of the image processor on a finished result (pure).
 *
 * A completed job may carry `warnings` (code + German message, see
 * processor/app/pipeline: mask.py assess_mask and pipeline.py): the result
 * was saved, but the employee should look at it. By what helps:
 * - "retake": the photo itself is the problem – e.g. it shows the car from
 *             the other side (plate_mirrored) → "Foto neu aufnehmen"
 * - "check":  something was cut from or touches the vehicle outline, or the
 *             cut-out is uncertain → check the result
 * - "info":   the result is usable but upscaled or smaller than usual →
 *             shown subtly, not counted as "bitte prüfen"
 * Unknown codes count as "check" and show the processor's own text – a hint
 * that asks for a check is never hidden.
 */
import type { ProcessingWarning } from "@/lib/domain/types";

export type ProcessingWarningKind = "retake" | "check" | "info";

const WARNING_KINDS: ReadonlyMap<string, ProcessingWarningKind> = new Map([
  ["plate_mirrored", "retake"],
  ["background_object_suspected", "retake"],
  ["background_object_removed", "check"],
  ["vehicle_occluded", "check"],
  ["mask_uncertain", "check"],
  ["busy_background", "check"],
  ["vehicle_not_detected", "check"],
  ["low_resolution", "info"],
  ["vehicle_reduced", "info"],
  ["vehicle_small", "info"],
]);

/** Longer messages are shortened to this many characters (the processor's texts are far shorter). */
const MAX_MESSAGE_LENGTH = 300;
const MAX_CODE_LENGTH = 40;
/** More hints than any job reports – a bound for stored data. */
const MAX_WARNINGS = 20;
/**
 * Bytes (UTF-8 JSON) all kept hints may take together. The database allows
 * 16 KB (vehicle_photos_processing_warnings_check, measured on its own JSON
 * text, which adds up to 4 spaces per hint) – this stays well below.
 */
const MAX_TOTAL_BYTES = 12_000;

const KIND_RANK: Record<ProcessingWarningKind, number> = { retake: 0, check: 1, info: 2 };

export function getWarningKind(code: string): ProcessingWarningKind {
  return WARNING_KINDS.get(code) ?? "check";
}

/** The hints that ask for a check or a retake – retakes first, otherwise in the processor's order. */
export function warningsToCheck(warnings: readonly ProcessingWarning[]): ProcessingWarning[] {
  const relevant = warnings.filter((warning) => getWarningKind(warning.code) !== "info");
  return [
    ...relevant.filter((warning) => getWarningKind(warning.code) === "retake"),
    ...relevant.filter((warning) => getWarningKind(warning.code) !== "retake"),
  ];
}

/** Informational hints only (shown subtly). */
export function infoWarnings(warnings: readonly ProcessingWarning[]): ProcessingWarning[] {
  return warnings.filter((warning) => getWarningKind(warning.code) === "info");
}

/** The result should be checked by the employee (a "check" or "retake" hint). */
export function needsResultCheck(warnings: readonly ProcessingWarning[]): boolean {
  return warnings.some((warning) => getWarningKind(warning.code) !== "info");
}

/** A new photo is the fix (e.g. the photo shows the other side of the car). */
export function hasRetakeWarning(warnings: readonly ProcessingWarning[]): boolean {
  return warnings.some((warning) => getWarningKind(warning.code) === "retake");
}

/** Control characters (the database cannot store a NUL) → spaces; lone surrogates → U+FFFD. */
function cleanText(text: string): string {
  const cleaned = text.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").trim();
  return typeof cleaned.toWellFormed === "function" ? cleaned.toWellFormed() : cleaned;
}

/** At most `max` characters – a longer text is cut at a word and ends with "…". */
function shorten(text: string, max: number): string {
  const characters = Array.from(text);
  if (characters.length <= max) return text;
  const cut = characters.slice(0, max - 1).join("");
  const space = cut.lastIndexOf(" ");
  return `${(space >= max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

function jsonBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

/**
 * Warnings from an untrusted source (processor answer, stored record, API
 * response) → valid hints in the processor's order: a German message
 * (longer than 300 characters: shortened, never dropped) and a code (default
 * "warning"); anything else is dropped. At most 20 hints of at most 12 KB
 * together (what the database stores) – within that bound, hints that ask
 * for a check or a retake are kept before informational ones. Copies the
 * entries; parsing parsed hints changes nothing.
 */
export function parseProcessingWarnings(value: unknown): ProcessingWarning[] {
  if (!Array.isArray(value)) return [];
  const parsed = value.flatMap((item: unknown): ProcessingWarning[] => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) return [];
    const { code, message } = item as Record<string, unknown>;
    const text = typeof message === "string" ? cleanText(message) : "";
    if (text.length === 0) return [];
    const name = typeof code === "string" ? Array.from(cleanText(code)).slice(0, MAX_CODE_LENGTH).join("").trim() : "";
    return [{ code: name || "warning", message: shorten(text, MAX_MESSAGE_LENGTH) }];
  });
  // Keep the most important hints within the bounds (a stable sort keeps the
  // processor's order within each kind).
  const byImportance = [...parsed.keys()].sort(
    (a, b) => KIND_RANK[getWarningKind(parsed[a]?.code ?? "")] - KIND_RANK[getWarningKind(parsed[b]?.code ?? "")],
  );
  const kept = new Set<number>();
  let bytes = 2; // "[]"
  for (const index of byImportance) {
    const size = jsonBytes(parsed[index]) + 1; // + ","
    if (kept.size === MAX_WARNINGS || bytes + size > MAX_TOTAL_BYTES) break;
    kept.add(index);
    bytes += size;
  }
  return parsed.filter((_, index) => kept.has(index));
}
