/**
 * Vehicle form validation and normalisation (pure).
 * Only manufacturer and model are required.
 */
import type { Vehicle, VehicleInput } from "@/lib/domain/types";

export const VEHICLE_FORM_FIELDS = [
  "manufacturer",
  "model",
  "color",
  "licensePlate",
  "vin",
  "mileage",
  "firstRegistration",
  "internalReference",
  "notes",
] as const;

export type VehicleFormField = (typeof VEHICLE_FORM_FIELDS)[number];
export type VehicleFormValues = Record<VehicleFormField, string>;
export type VehicleFormErrors = Partial<Record<VehicleFormField, string>>;

export type VehicleValidationResult =
  | { ok: true; value: VehicleInput }
  | { ok: false; errors: VehicleFormErrors };

export const VEHICLE_FIELD_LIMITS = {
  manufacturer: 60,
  model: 80,
  color: 40,
  licensePlate: 15,
  internalReference: 40,
  notes: 2000,
} as const;

export const MAX_MILEAGE = 2_000_000;

/** ISO 3779: 17 characters, letters I, O and Q are not allowed. */
const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/;
const LICENSE_PLATE_PATTERN = /^[A-ZÄÖÜ0-9 -]+$/;

export function emptyVehicleFormValues(): VehicleFormValues {
  return {
    manufacturer: "",
    model: "",
    color: "",
    licensePlate: "",
    vin: "",
    mileage: "",
    firstRegistration: "",
    internalReference: "",
    notes: "",
  };
}

export function vehicleToFormValues(vehicle: Vehicle): VehicleFormValues {
  return {
    manufacturer: vehicle.manufacturer,
    model: vehicle.model,
    color: vehicle.color ?? "",
    licensePlate: vehicle.licensePlate ?? "",
    vin: vehicle.vin ?? "",
    mileage: vehicle.mileage == null ? "" : String(vehicle.mileage),
    firstRegistration: vehicle.firstRegistration ?? "",
    internalReference: vehicle.internalReference ?? "",
    notes: vehicle.notes ?? "",
  };
}

function optionalText(value: string): string | null {
  const trimmed = value.trim().replace(/\s+/g, " ");
  return trimmed === "" ? null : trimmed;
}

export function normalizeLicensePlate(value: string): string | null {
  const normalized = value.trim().toUpperCase().replace(/\s+/g, " ");
  return normalized === "" ? null : normalized;
}

export function normalizeVin(value: string): string | null {
  const normalized = value.replace(/[\s-]/g, "").toUpperCase();
  return normalized === "" ? null : normalized;
}

/** Accepts "123456", "123.456", "123 456 km". */
export function parseMileage(value: string): number | null | "invalid" {
  const cleaned = value.trim().toLowerCase().replace(/km$/, "").replace(/[.\s]/g, "");
  if (cleaned === "") return null;
  if (!/^\d+$/.test(cleaned)) return "invalid";
  const mileage = Number.parseInt(cleaned, 10);
  if (!Number.isSafeInteger(mileage) || mileage > MAX_MILEAGE) return "invalid";
  return mileage;
}

/** Accepts ISO "YYYY-MM-DD" (date input) or German "TT.MM.JJJJ". */
export function parseGermanOrIsoDate(value: string): string | null | "invalid" {
  const trimmed = value.trim();
  if (trimmed === "") return null;

  let year: number;
  let month: number;
  let day: number;
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  const german = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(trimmed);
  if (iso) {
    [year, month, day] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
  } else if (german) {
    [day, month, year] = [Number(german[1]), Number(german[2]), Number(german[3])];
  } else {
    return "invalid";
  }

  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return "invalid";
  }
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export interface ValidateVehicleOptions {
  /** Injected for deterministic tests. */
  today?: Date;
}

export function validateVehicleForm(
  values: VehicleFormValues,
  options: ValidateVehicleOptions = {},
): VehicleValidationResult {
  const errors: VehicleFormErrors = {};
  const today = options.today ?? new Date();

  const manufacturer = optionalText(values.manufacturer);
  if (!manufacturer) errors.manufacturer = "Bitte geben Sie den Hersteller an.";
  else if (manufacturer.length > VEHICLE_FIELD_LIMITS.manufacturer)
    errors.manufacturer = `Maximal ${VEHICLE_FIELD_LIMITS.manufacturer} Zeichen.`;

  const model = optionalText(values.model);
  if (!model) errors.model = "Bitte geben Sie das Modell an.";
  else if (model.length > VEHICLE_FIELD_LIMITS.model)
    errors.model = `Maximal ${VEHICLE_FIELD_LIMITS.model} Zeichen.`;

  const color = optionalText(values.color);
  if (color && color.length > VEHICLE_FIELD_LIMITS.color)
    errors.color = `Maximal ${VEHICLE_FIELD_LIMITS.color} Zeichen.`;

  const licensePlate = normalizeLicensePlate(values.licensePlate);
  if (licensePlate) {
    if (licensePlate.length > VEHICLE_FIELD_LIMITS.licensePlate)
      errors.licensePlate = `Maximal ${VEHICLE_FIELD_LIMITS.licensePlate} Zeichen.`;
    else if (!LICENSE_PLATE_PATTERN.test(licensePlate))
      errors.licensePlate = "Das Kennzeichen enthält ungültige Zeichen.";
  }

  const vin = normalizeVin(values.vin);
  if (vin && !VIN_PATTERN.test(vin))
    errors.vin = "Die FIN muss aus 17 Zeichen bestehen (ohne I, O und Q).";

  const mileage = parseMileage(values.mileage);
  if (mileage === "invalid")
    errors.mileage = "Bitte geben Sie einen gültigen Kilometerstand an.";

  const firstRegistration = parseGermanOrIsoDate(values.firstRegistration);
  if (firstRegistration === "invalid") {
    errors.firstRegistration = "Bitte geben Sie ein gültiges Datum an.";
  } else if (firstRegistration) {
    const todayIso = today.toISOString().slice(0, 10);
    if (firstRegistration > todayIso)
      errors.firstRegistration = "Die Erstzulassung darf nicht in der Zukunft liegen.";
    else if (firstRegistration < "1900-01-01")
      errors.firstRegistration = "Bitte geben Sie ein gültiges Datum an.";
  }

  const internalReference = optionalText(values.internalReference);
  if (
    internalReference &&
    internalReference.length > VEHICLE_FIELD_LIMITS.internalReference
  )
    errors.internalReference = `Maximal ${VEHICLE_FIELD_LIMITS.internalReference} Zeichen.`;

  const notes = values.notes.trim() === "" ? null : values.notes.trim();
  if (notes && notes.length > VEHICLE_FIELD_LIMITS.notes)
    errors.notes = `Maximal ${VEHICLE_FIELD_LIMITS.notes} Zeichen.`;

  if (Object.keys(errors).length > 0 || !manufacturer || !model) {
    return { ok: false, errors };
  }

  return {
    ok: true,
    value: {
      manufacturer,
      model,
      color,
      licensePlate,
      vin,
      mileage: mileage === "invalid" ? null : mileage,
      firstRegistration: firstRegistration === "invalid" ? null : firstRegistration,
      internalReference,
      notes,
    },
  };
}
