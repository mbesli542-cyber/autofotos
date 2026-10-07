import { Camera, CarFront, ChevronRight } from "lucide-react";
import Link from "next/link";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { VehicleSummary } from "@/lib/domain/types";
import { cn } from "@/lib/cn";
import { formatDate, vehicleDisplayName } from "@/lib/format";

/** Tappable vehicle row: thumbnail, name, photo progress, date and status. */
export function VehicleCard({ summary, href }: { summary: VehicleSummary; href?: string }) {
  const { vehicle, capturedRequiredCount, requiredCount, thumbnailUrl } = summary;
  const name = vehicleDisplayName(vehicle);
  const complete = capturedRequiredCount >= requiredCount;

  return (
    <Link
      href={href ?? `/fahrzeuge/${vehicle.id}`}
      className="group flex items-center gap-3.5 rounded-2xl border border-ae-border bg-ae-surface p-3 shadow-[var(--shadow-card)] transition-colors hover:border-ae-border-strong hover:bg-ae-surface-2 active:bg-ae-surface-2"
    >
      <div className="relative aspect-[4/3] w-28 shrink-0 overflow-hidden rounded-xl bg-ae-surface-3 sm:w-32">
        {thumbnailUrl ? (
          <img src={thumbnailUrl} alt="" className="size-full object-cover" loading="lazy" />
        ) : (
          <div className="flex size-full items-center justify-center text-ae-subtle">
            <CarFront className="size-8" aria-hidden />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <h3 className="line-clamp-2 text-[15px] leading-snug font-semibold">{name}</h3>
        <p
          className={cn(
            "mt-1 flex items-center gap-1.5 text-sm",
            complete ? "text-ae-success" : "text-ae-muted",
          )}
        >
          <Camera className="size-3.5" aria-hidden />
          <span>
            {capturedRequiredCount} / {requiredCount} Fotos
          </span>
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <StatusBadge status={vehicle.status} />
          <time className="text-xs text-ae-subtle" dateTime={vehicle.createdAt}>
            {formatDate(vehicle.createdAt)}
          </time>
        </div>
      </div>

      <ChevronRight
        className="size-5 shrink-0 text-ae-subtle transition-transform group-hover:translate-x-0.5"
        aria-hidden
      />
    </Link>
  );
}
