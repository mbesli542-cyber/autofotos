/**
 * "Alle Fotos speichern": which file of every shot goes into the listing set
 * (pure).
 *
 * In shot-template order (sortPhotosByShotOrder), for every template shot:
 * - processed: its processed result (AutoExperten showroom / original environment)
 * - original:  not processed (yet) → the original, marked in the summary
 * - retake:    the automatic check or a processing rejected the photo
 *              ("muss neu aufgenommen werden") – only exported when the
 *              employee confirms it explicitly. Known on every device: the
 *              rejection is kept with the photo (processing-warnings.ts
 *              storedRejection), the device's checks add what is newer.
 * - missing:   no photo saved for the shot
 * - unsaved:   a newer capture is still being saved (or could not be saved)
 *              on this device – the saved photo it replaces is not exported
 * Extra photos (never processed) follow as originals, numbered after the
 * template (16, 17, … – exportOrderOf).
 *
 * Every file is named AE_{ref}_{nn}_{shot}.{ext} (buildExportFileName), so
 * the file names sort in listing order. A file that cannot be loaded can be
 * left out, a missing processed file replaced by the original – only on the
 * employee's choice (ExportChoices).
 */
import type { PhotoVersion, Vehicle, VehiclePhoto } from "@/lib/domain/types";
import { buildExportFileName, getStoragePathExtension } from "@/lib/naming/file-naming";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import {
  hasRetakeWarning,
  needsResultCheck,
  storedRejection,
  warningsToCheck,
} from "@/lib/processing/processing-warnings";
import { SHOT_CHECK_LABELS, type ShotCheckState } from "@/lib/processing/shot-check";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import { EXTRA_SHOT_ORDER_OFFSET, type ShotTemplate } from "@/lib/shots/shot-template";

export type ExportItemStatus = "processed" | "original" | "retake" | "missing" | "unsaved" | "extra";

export interface ExportItem {
  /** Shot key. */
  key: string;
  /** Position in the listing order and in the file name (template order; extra photos after it). */
  order: number;
  title: string;
  status: ExportItemStatus;
  photo: VehiclePhoto | null;
  /** The file exported for the shot; null: nothing to export. */
  version: PhotoVersion | null;
  /** AE_{ref}_{nn}_{shot}.{ext}; null when nothing can be exported. */
  fileName: string | null;
  /** The original's export name when the processed result is exported (the way out if its file is gone). */
  originalFileName: string | null;
  /** The original instead of the missing processed file – chosen by the employee (withOriginal). */
  originalInstead?: boolean;
  /** The processor asks to check the processed result. */
  needsCheck: boolean;
  /** German: the reason to retake, or the processor's hint on the result. */
  note: string | null;
}

export interface ExportCounts {
  processed: number;
  original: number;
  retake: number;
  missing: number;
  unsaved: number;
  extra: number;
  /** Processed results the processor asks to check (part of `processed`). */
  check: number;
}

export interface ExportPlan {
  items: ExportItem[];
  counts: ExportCounts;
  /** Every template shot has a processed result that needs no retake. */
  complete: boolean;
}

export type ExportVehicle = Pick<Vehicle, "id" | "internalReference">;

/** Slot fields the plan needs (photo-slots.ts). */
export type ExportSlot = Pick<PhotoSlot, "key" | "order" | "title" | "pending"> & { photo: VehiclePhoto | null };

/**
 * The number of a shot in the listing and in its file name: the template's
 * order; extra photos ("Zusatzfoto n", order 100 + n) come right after the
 * template – 16, 17, … with 15 template shots – so the file names sort in
 * listing order, also where "_101_" would land between "_09_" and "_10_".
 */
export function exportOrderOf(
  template: Pick<ShotTemplate, "shots">,
  photo: Pick<VehiclePhoto, "shotKey" | "shotOrder">,
): number {
  const shot = template.shots.find((entry) => entry.key === photo.shotKey);
  if (shot) return shot.order;
  if (photo.shotOrder <= EXTRA_SHOT_ORDER_OFFSET) return photo.shotOrder;
  const last = template.shots.reduce((max, entry) => Math.max(max, entry.order), 0);
  return last + (photo.shotOrder - EXTRA_SHOT_ORDER_OFFSET);
}

/** The export file name of one version of a photo (processed results are JPEGs). */
export function exportFileNameFor(
  template: Pick<ShotTemplate, "shots">,
  vehicle: ExportVehicle,
  photo: Pick<VehiclePhoto, "shotKey" | "shotOrder" | "originalStoragePath">,
  version: PhotoVersion,
): string {
  const extension =
    version === "processed" ? "jpg" : (getStoragePathExtension(photo.originalStoragePath) ?? "jpg");
  return buildExportFileName({
    internalReference: vehicle.internalReference,
    vehicleId: vehicle.id,
    shotOrder: exportOrderOf(template, photo),
    shotKey: photo.shotKey,
    extension: extension === "jpeg" ? "jpg" : extension,
  });
}

interface PlanContext {
  template: ShotTemplate;
  vehicle: ExportVehicle;
}

const UNSAVED_NOTE = "Foto wird noch gespeichert.";
const SAVE_FAILED_NOTE = "Foto konnte nicht gespeichert werden.";

function item(
  { template, vehicle }: PlanContext,
  slot: ExportSlot,
  status: ExportItemStatus,
  version: PhotoVersion | null,
  note: string | null = null,
): ExportItem {
  const photo = slot.photo;
  const needsCheck =
    version === "processed" && photo !== null && needsResultCheck(photo.processingWarnings);
  return {
    key: slot.key,
    order: photo ? exportOrderOf(template, photo) : exportOrderOf(template, { shotKey: slot.key, shotOrder: slot.order }),
    title: slot.title,
    status,
    photo,
    version,
    fileName: photo && version ? exportFileNameFor(template, vehicle, photo, version) : null,
    originalFileName:
      photo && version === "processed" ? exportFileNameFor(template, vehicle, photo, "original") : null,
    needsCheck,
    note: note ?? (needsCheck && photo ? (warningsToCheck(photo.processingWarnings)[0]?.message ?? null) : null),
  };
}

function unsavedItem(context: PlanContext, slot: ExportSlot): ExportItem {
  const note = slot.pending?.status === "failed" ? SAVE_FAILED_NOTE : UNSAVED_NOTE;
  return item(context, { ...slot, photo: null }, "unsaved", null, note);
}

/**
 * Why the shot's photo must be retaken, or null: the shot's check state
 * (shot-check.ts – it reads what is saved with the photo, too); without one,
 * what is saved with the photo: the processor's stored rejection, or a
 * result whose hint says the photo itself is the problem.
 */
function retakeReason(photo: VehiclePhoto, check: ShotCheckState | undefined): string | null {
  if (check) return check.kind === "retake" ? check.reason : null;
  const rejection = storedRejection(photo);
  if (rejection !== null) return rejection;
  if (photo.processedStoragePath && hasRetakeWarning(photo.processingWarnings)) {
    return warningsToCheck(photo.processingWarnings)[0]?.message ?? null;
  }
  return null;
}

function templateItem(context: PlanContext, slot: ExportSlot, check: ShotCheckState | undefined): ExportItem {
  if (slot.pending) return unsavedItem(context, slot);
  const photo = slot.photo;
  if (!photo) return item(context, slot, "missing", null);
  const processed = photo.processedStoragePath !== null;
  const retake = retakeReason(photo, check);
  if (retake !== null) return item(context, slot, "retake", processed ? "processed" : "original", retake);
  if (processed) return item(context, slot, "processed", "processed");
  return item(
    context,
    slot,
    "original",
    "original",
    check?.kind === "checking" ? "Wird gerade geprüft." : null,
  );
}

function extraItem(context: PlanContext, slot: ExportSlot): ExportItem {
  if (slot.pending) return unsavedItem(context, slot);
  return item(context, slot, slot.photo ? "extra" : "missing", slot.photo ? "original" : null);
}

/**
 * The listing set of a vehicle: one item per template shot (in template
 * order), then one per extra photo. `checks`: the shots' check states
 * (shot-check.ts) – the same the vehicle page shows.
 */
export function buildExportPlan({
  template,
  vehicle,
  slots,
  checks,
}: {
  template: ShotTemplate;
  vehicle: ExportVehicle;
  slots: { required: readonly ExportSlot[]; extras: readonly ExportSlot[] };
  checks?: ReadonlyMap<string, ShotCheckState>;
}): ExportPlan {
  const context: PlanContext = { template, vehicle };
  // Listing order: the template's, extra photos after it (never by time).
  const items = sortPhotosByShotOrder(template, [
    ...slots.required.map((slot) => templateItem(context, slot, checks?.get(slot.key))),
    ...slots.extras.filter((slot) => slot.photo || slot.pending).map((slot) => extraItem(context, slot)),
  ].map((entry) => ({ shotKey: entry.key, shotOrder: entry.order, entry }))).map(({ entry }) => entry);
  const count = (status: ExportItemStatus) => items.filter((entry) => entry.status === status).length;
  const counts: ExportCounts = {
    processed: count("processed"),
    original: count("original"),
    retake: count("retake"),
    missing: count("missing"),
    unsaved: count("unsaved"),
    extra: count("extra"),
    check: items.filter((entry) => entry.status === "processed" && entry.needsCheck).length,
  };
  return { items, counts, complete: counts.processed === slots.required.length };
}

/** What the employee chose in the summary. */
export interface ExportChoices {
  /** Rejected shots ("retake") are saved too. */
  includeRetakes?: boolean;
  /** Shot keys left out ("Ohne dieses Foto speichern" – the file could not be loaded). */
  leaveOut?: ReadonlySet<string>;
  /** Shot keys whose processed file is gone: their original instead ("Original verwenden"). */
  useOriginal?: ReadonlySet<string>;
}

/** The item with its original instead of the processed result (named for the original). */
export function withOriginal(entry: ExportItem): ExportItem {
  if (entry.version !== "processed" || entry.originalFileName === null) return entry;
  return {
    ...entry,
    version: "original",
    fileName: entry.originalFileName,
    originalFileName: null,
    originalInstead: true,
    needsCheck: false,
  };
}

/**
 * The items whose files are saved, in listing order: everything exportable –
 * rejected shots ("retake") only with `includeRetakes`; without the shots
 * left out, with the original where the employee chose it.
 */
export function selectExportItems(
  plan: ExportPlan,
  { includeRetakes = false, leaveOut, useOriginal }: ExportChoices = {},
): ExportItem[] {
  return plan.items
    .filter(
      (entry) =>
        entry.version !== null && (entry.status !== "retake" || includeRetakes) && !leaveOut?.has(entry.key),
    )
    .map((entry) => (useOriginal?.has(entry.key) ? withOriginal(entry) : entry));
}

/** "1 Foto" / "3 Fotos". */
export function photoCount(count: number): string {
  return count === 1 ? "1 Foto" : `${count} Fotos`;
}

/**
 * The summary line of the plan: "15 Fotos bereit" when every template shot
 * is processed, otherwise e.g. "13 bearbeitet · 1 noch nicht bearbeitet ·
 * 1 muss neu aufgenommen werden".
 */
export function describeExportPlan(plan: ExportPlan): string {
  const { counts } = plan;
  const extras = counts.extra > 0 ? ` · ${counts.extra === 1 ? "1 Zusatzfoto" : `${counts.extra} Zusatzfotos`}` : "";
  if (plan.complete) return `${photoCount(counts.processed)} bereit${extras}`;
  const parts = [
    counts.processed > 0 ? `${counts.processed} bearbeitet` : null,
    counts.original > 0 ? `${counts.original} noch nicht bearbeitet` : null,
    counts.retake > 0
      ? counts.retake === 1
        ? "1 muss neu aufgenommen werden"
        : `${counts.retake} müssen neu aufgenommen werden`
      : null,
    counts.missing > 0 ? (counts.missing === 1 ? "1 fehlt" : `${counts.missing} fehlen`) : null,
    counts.unsaved > 0 ? `${counts.unsaved} noch nicht gespeichert` : null,
  ].filter((part): part is string => part !== null);
  return `${parts.join(" · ")}${extras}`;
}

/** German status of an item in the summary's list of shots to look at. */
export function exportItemLabel(entry: ExportItem): string {
  switch (entry.status) {
    case "processed":
      // The one wording of a result to look at (shot-check.ts SHOT_CHECK_LABELS.hint), as in the large view.
      return entry.needsCheck ? `Bearbeitet · ${SHOT_CHECK_LABELS.hint}` : "Bearbeitet";
    case "original":
      return "Noch nicht bearbeitet – das Original wird gespeichert";
    case "retake":
      return "Muss neu aufgenommen werden";
    case "missing":
      return "Kein Foto vorhanden";
    case "unsaved":
      return "Noch nicht gespeichert";
    case "extra":
      return "Zusatzfoto – Original";
  }
}

/** The items the summary lists (everything but a processed result without hints and extra photos). */
export function itemsToReview(plan: ExportPlan): ExportItem[] {
  return plan.items.filter(
    (entry) => (entry.status !== "processed" || entry.needsCheck) && entry.status !== "extra",
  );
}

/** One row of the summary's list: one shot, or several shots with the same status. */
export interface ExportReviewRow {
  /** React key. */
  key: string;
  /** Shot numbers, e.g. "04" or "01–08, 10". */
  numbers: string;
  /** The shot's title, or "15 Fotos" for a group. */
  title: string;
  status: ExportItemStatus;
  label: string;
  note: string | null;
  /** Shot key to open in the large view (single rows only). */
  shotKey: string | null;
}

/** Shots of the same status from this many on are one row (rejected shots and hints always have their own row). */
export const REVIEW_GROUP_FROM = 3;

/** Shot numbers with ranges: [1,2,3,5] → "01–03, 05". */
export function formatShotNumbers(orders: readonly number[]): string {
  const sorted = [...new Set(orders)].sort((a, b) => a - b);
  const pad = (order: number) => String(order).padStart(2, "0");
  const ranges: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let end = i;
    while (end + 1 < sorted.length && (sorted[end + 1] ?? 0) === (sorted[end] ?? 0) + 1) end += 1;
    const first = sorted[i] ?? 0;
    const last = sorted[end] ?? 0;
    ranges.push(end - i >= 2 ? `${pad(first)}–${pad(last)}` : end > i ? `${pad(first)}, ${pad(last)}` : pad(first));
    i = end + 1;
  }
  return ranges.join(", ");
}

const GROUP_LABELS: Partial<Record<ExportItemStatus, string>> = {
  original: "Noch nicht bearbeitet – die Originale werden gespeichert",
  missing: "Kein Foto vorhanden",
  unsaved: "Noch nicht gespeichert",
};

/**
 * The summary's list of shots to look at, in listing order: every rejected
 * shot and every result to check on its own row (with its reason); shots
 * that are only "not processed yet", missing or unsaved become one row per
 * status once there are REVIEW_GROUP_FROM of them.
 */
export function reviewRows(plan: ExportPlan): ExportReviewRow[] {
  const items = itemsToReview(plan);
  const grouped = new Set<ExportItemStatus>(
    (Object.keys(GROUP_LABELS) as ExportItemStatus[]).filter(
      (status) => items.filter((entry) => entry.status === status).length >= REVIEW_GROUP_FROM,
    ),
  );
  const rows: ExportReviewRow[] = [];
  const emitted = new Set<ExportItemStatus>();
  for (const entry of items) {
    if (grouped.has(entry.status)) {
      if (emitted.has(entry.status)) continue;
      emitted.add(entry.status);
      const members = items.filter((other) => other.status === entry.status);
      rows.push({
        key: `group-${entry.status}`,
        numbers: formatShotNumbers(members.map((member) => member.order)),
        title: photoCount(members.length),
        status: entry.status,
        label: GROUP_LABELS[entry.status] ?? exportItemLabel(entry),
        note: null,
        shotKey: null,
      });
      continue;
    }
    rows.push({
      key: entry.key,
      numbers: String(entry.order).padStart(2, "0"),
      title: entry.title,
      status: entry.status,
      label: exportItemLabel(entry),
      note: entry.note,
      shotKey: entry.status === "missing" ? null : entry.key,
    });
  }
  return rows;
}

