import { Pencil } from "lucide-react";
import Link from "next/link";
import type { Vehicle } from "@/lib/domain/types";
import { formatDate, formatMileage } from "@/lib/format";

/** "Fahrzeugdaten" definition list. */
export function VehicleInfoCard({ vehicle, editHref }: { vehicle: Vehicle; editHref?: string }) {
  const rows: Array<[string, string]> = [
    ["Hersteller", vehicle.manufacturer],
    ["Modell", vehicle.model],
    ["Farbe", vehicle.color ?? "–"],
    ["Kennzeichen", vehicle.licensePlate ?? "–"],
    ["FIN", vehicle.vin ?? "–"],
    ["Kilometerstand", formatMileage(vehicle.mileage)],
    ["Erstzulassung", formatDate(vehicle.firstRegistration)],
    ["Interne Fahrzeugnummer", vehicle.internalReference ?? "–"],
    ["Erfasst am", formatDate(vehicle.createdAt)],
  ];

  return (
    <section
      aria-labelledby="vehicle-data-title"
      className="rounded-2xl border border-ae-border bg-ae-surface p-4 sm:p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 id="vehicle-data-title" className="font-semibold">
          Fahrzeugdaten
        </h2>
        {editHref && (
          <Link
            href={editHref}
            className="-mr-2 inline-flex h-9 items-center gap-1.5 rounded-lg px-2 text-sm font-medium text-ae-blue hover:bg-ae-surface-2"
          >
            <Pencil className="size-4" aria-hidden />
            Ändern
          </Link>
        )}
      </div>
      <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 border-b border-ae-border/60 py-2.5 sm:block">
            <dt className="text-sm text-ae-muted">{label}</dt>
            <dd className="text-right text-sm font-medium break-all sm:mt-0.5 sm:text-left">{value}</dd>
          </div>
        ))}
      </dl>
      {vehicle.notes && (
        <div className="mt-3">
          <h3 className="text-sm text-ae-muted">Notizen</h3>
          <p className="mt-1 text-sm whitespace-pre-line">{vehicle.notes}</p>
        </div>
      )}
    </section>
  );
}
