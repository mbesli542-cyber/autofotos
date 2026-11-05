import { RotateCcw } from "lucide-react";
import type { VehicleStatus } from "@/lib/domain/types";
import { cn } from "@/lib/cn";
import { VEHICLE_STATUS_LABELS } from "@/lib/vehicles/status";

/** Text and border colour per status. */
const TONES: Record<VehicleStatus, string> = {
  new: "text-ae-muted border-ae-border-strong",
  capturing: "text-ae-warning border-ae-warning/30",
  complete: "text-ae-success border-ae-success/30",
  processed: "text-[#5aa6ff] border-ae-blue/35",
};

/** Translucent status tint – only readable on the dark app background. */
const TINTS: Record<VehicleStatus, string> = {
  new: "bg-ae-surface-3",
  capturing: "bg-ae-warning/12",
  complete: "bg-ae-success/12",
  processed: "bg-ae-blue/15",
};

/**
 * Vehicle status chip. `onImage`: on top of a photo (e.g. a bright showroom
 * wall) the chip gets an opaque dark background instead of the tint, so the
 * status stays readable on any picture.
 */
export function StatusBadge({
  status,
  onImage = false,
  className,
}: {
  status: VehicleStatus;
  onImage?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        TONES[status],
        onImage ? "bg-ae-bg shadow-[0_1px_4px_rgb(0_0_0/0.35)]" : TINTS[status],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {VEHICLE_STATUS_LABELS[status]}
    </span>
  );
}

/**
 * "Neu aufnehmen" chip: `count` shots of the vehicle were rejected by the
 * automatic check or a processing (src/lib/processing/retake-summary.ts).
 */
export function RetakeBadge({
  count,
  onImage = false,
  className,
}: {
  count: number;
  onImage?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-ae-danger/40 px-2.5 py-0.5 text-xs font-semibold text-ae-danger",
        onImage ? "bg-ae-bg shadow-[0_1px_4px_rgb(0_0_0/0.35)]" : "bg-ae-danger/12",
        className,
      )}
    >
      <RotateCcw className="size-3" aria-hidden />
      Neu aufnehmen
      {count > 1 && (
        <span className="tabular-nums" aria-hidden>
          · {count}
        </span>
      )}
      <span className="sr-only">{count === 1 ? "(1 Foto)" : `(${count} Fotos)`}</span>
    </span>
  );
}
