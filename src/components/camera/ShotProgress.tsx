import { Check, CloudOff } from "lucide-react";
import { cn } from "@/lib/cn";
import type { PhotoSlot } from "@/lib/photos/photo-slots";

/**
 * Step indicator: one segment per required shot. Completed shots show a
 * checkmark, the current shot is highlighted. Segments are tappable.
 */
export function ShotProgress({
  slots,
  currentKey,
  onSelect,
}: {
  slots: readonly PhotoSlot[];
  currentKey: string;
  onSelect: (key: string) => void;
}) {
  return (
    <ol className="flex w-full gap-[3px]" aria-label="Aufnahmefortschritt">
      {slots.map((slot) => {
        const current = slot.key === currentKey;
        const failed = slot.pending?.status === "failed";
        const done = Boolean(slot.photo) || Boolean(slot.pending);
        const state = failed ? "nicht gespeichert" : done ? "aufgenommen" : "offen";
        return (
          <li key={slot.key} className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => onSelect(slot.key)}
              aria-label={`${slot.order}. ${slot.title} – ${state}`}
              aria-current={current ? "step" : undefined}
              className={cn(
                "flex h-[22px] w-full items-center justify-center rounded-[5px] text-[10px] font-bold tabular-nums transition-colors",
                failed
                  ? "bg-ae-warning text-black"
                  : done
                    ? "bg-ae-blue text-white"
                    : "bg-white/12 text-white/55",
                current && "ring-2 ring-white ring-offset-1 ring-offset-black",
              )}
            >
              {failed ? (
                <CloudOff className="size-3" aria-hidden />
              ) : done ? (
                <Check className="size-3" strokeWidth={3.5} aria-hidden />
              ) : (
                slot.order
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
