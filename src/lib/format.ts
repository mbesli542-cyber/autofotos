/** German display formatting helpers. */

const dateFormatter = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  timeZone: "Europe/Berlin",
});

const dateTimeFormatter = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Berlin",
});

const numberFormatter = new Intl.NumberFormat("de-DE");

/** "2026-10-07T…" → "07.10.2026" */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
  return Number.isNaN(date.getTime()) ? "–" : dateFormatter.format(date);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "–" : dateTimeFormatter.format(date);
}

/** 123456 → "123.456 km" */
export function formatMileage(mileage: number | null | undefined): string {
  return mileage == null ? "–" : `${numberFormatter.format(mileage)} km`;
}

export function vehicleDisplayName(vehicle: {
  manufacturer: string;
  model: string;
}): string {
  return `${vehicle.manufacturer} ${vehicle.model}`.trim();
}

export function pluralizePhotos(count: number): string {
  return count === 1 ? "1 Foto" : `${count} Fotos`;
}
