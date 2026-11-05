/**
 * "Alle Fotos speichern": which file of every shot goes into the listing set
 * (pure).
 *
 * In shot-template order (sortPhotosByShotOrder), for every template shot:
 * - processed: its processed result (AutoExperten showroom / original environment)
 * - original:  not processed (yet) → the original, marked in the summary
 * - retake:    the automatic check or a processing rejected the photo
 *              ("muss neu aufgenommen werden") – only exported when the
 *              employee confirms it explicitly
 * - missing:   no photo saved for the shot
 * - unsaved:   a newer capture is still being saved (or could not be saved)
 *              on this device – the saved photo it replaces is not exported
 * Extra photos (never processed) follow as originals.
 *
 * Every file is named AE_{ref}_{nn}_{shot}.{ext} (buildExportFileName), so
 * the file names sort in listing order.
 */
import type { PhotoVersion, Vehicle, VehiclePhoto } from "@/lib/domain/types";
import { buildExportFileName, getStoragePathExtension } from "@/lib/naming/file-naming";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import { needsResultCheck, warningsToCheck } from "@/lib/processing/processing-warnings";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { sortPhotosByShotOrder } from "@/lib/shots/shot-progress";
import type { ShotTemplate } from "@/lib/shots/shot-template";

export type ExportItemStatus = "processed" | "original" | "retake" | "missing" | "unsaved" | "extra";

export interface ExportItem {
  /** Shot key. */
  key: string;
  /** Position in the listing order (template order; extra photos 101+). */
  order: number;
  title: string;
  status: ExportItemStatus;
  photo: VehiclePhoto | null;
  /** The file exported for the shot; null: nothing to export. */
  version: PhotoVersion | null;
  /** AE_{ref}_{nn}_{shot}.{ext}; null when nothing can be exported. */
  fileName: string | null;
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

/** The export file name of one version of a photo (processed results are JPEGs). */
export function exportFileNameFor(vehicle: ExportVehicle, photo: VehiclePhoto, version: PhotoVersion): string {
  const extension =
    version === "processed" ? "jpg" : (getStoragePathExtension(photo.originalStoragePath) ?? "jpg");
  return buildExportFileName({
    internalReference: vehicle.internalReference,
    vehicleId: vehicle.id,
    shotOrder: photo.shotOrder,
    shotKey: photo.shotKey,
    extension: extension === "jpeg" ? "jpg" : extension,
  });
}

function item(
  vehicle: ExportVehicle,
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
    order: slot.order,
    title: slot.title,
    status,
    photo,
    version,
    fileName: photo && version ? exportFileNameFor(vehicle, photo, version) : null,
    needsCheck,
    note: note ?? (needsCheck && photo ? (warningsToCheck(photo.processingWarnings)[0]?.message ?? null) : null),
  };
}

function templateItem(vehicle: ExportVehicle, slot: ExportSlot, check: ShotCheckState | undefined): ExportItem {
  if (slot.pending) {
    return item(
      vehicle,
      { ...slot, photo: null },
      "unsaved",
      null,
      slot.pending.status === "failed" ? "Foto konnte nicht gespeichert werden." : "Foto wird noch gespeichert.",
    );
  }
  const photo = slot.photo;
  if (!photo) return item(vehicle, slot, "missing", null);
  const processed = photo.processedStoragePath !== null;
  if (check?.kind === "retake") return item(vehicle, slot, "retake", processed ? "processed" : "original", check.reason);
  if (processed) return item(vehicle, slot, "processed", "processed");
  return item(
    vehicle,
    slot,
    "original",
    "original",
    check?.kind === "checking" ? "Wird gerade geprüft." : null,
  );
}

function extraItem(vehicle: ExportVehicle, slot: ExportSlot): ExportItem {
  if (slot.pending) return templateItem(vehicle, slot, undefined);
  return item(vehicle, slot, slot.photo ? "extra" : "missing", slot.photo ? "original" : null);
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
  // Listing order: the template's, extra photos after it (never by time).
  const items = sortPhotosByShotOrder(template, [
    ...slots.required.map((slot) => templateItem(vehicle, slot, checks?.get(slot.key))),
    ...slots.extras.filter((slot) => slot.photo || slot.pending).map((slot) => extraItem(vehicle, slot)),
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

/**
 * The items whose files are saved, in listing order: everything exportable –
 * rejected shots ("retake") only with `includeRetakes`.
 */
export function selectExportItems(plan: ExportPlan, { includeRetakes = false } = {}): ExportItem[] {
  return plan.items.filter((entry) => entry.version !== null && (entry.status !== "retake" || includeRetakes));
}

/** Items that may be loaded for the export (every item with a file, rejected shots included). */
export function exportableItems(plan: ExportPlan): ExportItem[] {
  return selectExportItems(plan, { includeRetakes: true });
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
    `${counts.processed} bearbeitet`,
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
      return entry.needsCheck ? "Bearbeitet – bitte Ergebnis prüfen" : "Bearbeitet";
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
