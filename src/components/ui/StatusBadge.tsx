import type { VehicleStatus } from "@/lib/domain/types";
import { cn } from "@/lib/cn";
import { VEHICLE_STATUS_LABELS } from "@/lib/vehicles/status";

const STYLES: Record<VehicleStatus, string> = {
  new: "bg-ae-surface-3 text-ae-muted border-ae-border-strong",
  capturing: "bg-ae-warning/12 text-ae-warning border-ae-warning/30",
  complete: "bg-ae-success/12 text-ae-success border-ae-success/30",
  processed: "bg-ae-blue/15 text-[#5aa6ff] border-ae-blue/35",
};

export function StatusBadge({ status, className }: { status: VehicleStatus; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        STYLES[status],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {VEHICLE_STATUS_LABELS[status]}
    </span>
  );
}
