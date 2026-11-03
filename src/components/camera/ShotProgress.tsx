import { Check, CloudOff, RotateCcw } from "lucide-react";
import { cn } from "@/lib/cn";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";

function describe(slot: PhotoSlot, check: ShotCheckState | undefined): string {
  if (slot.pending?.status === "failed") return "nicht gespeichert";
  if (!slot.photo && !slot.pending) return "offen";
  switch (check?.kind) {
    case "checking":
      return "aufgenommen, wird geprüft";
    case "ok":
      return check.hint ? `geprüft, bitte Ergebnis prüfen: ${check.hint}` : "geprüft, passt";
    case "retake":
      return `bitte neu aufnehmen: ${check.reason}`;
    default:
      return "aufgenommen";
  }
}

/**
 * Step indicator: one segment per required shot. Completed shots show a
 * checkmark, the current shot is highlighted. Segments are tappable. With
 * the automatic check (photo-checks.ts) a segment also shows its result:
 * pulsing while checked, green when it passed, red when the photo must be
 * retaken (tapping it jumps to that shot).
 */
export function ShotProgress({
  slots,
  currentKey,
  onSelect,
  checks,
}: {
  slots: readonly PhotoSlot[];
  currentKey: string;
  onSelect: (key: string) => void;
  /** Check state per shot key (automatic check after the capture). */
  checks?: ReadonlyMap<string, ShotCheckState>;
}) {
  return (
    <ol className="flex w-full gap-[3px]" aria-label="Aufnahmefortschritt">
      {slots.map((slot) => {
        const current = slot.key === currentKey;
        const failed = slot.pending?.status === "failed";
        const done = Boolean(slot.photo) || Boolean(slot.pending);
        const check = done && !failed ? checks?.get(slot.key) : undefined;
        const kind = check?.kind ?? "none";
        return (
          <li key={slot.key} className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => onSelect(slot.key)}
              aria-label={`${slot.order}. ${slot.title} – ${describe(slot, check)}`}
              aria-current={current ? "step" : undefined}
              data-check={done && !failed ? kind : undefined}
              className={cn(
                "flex h-[22px] w-full items-center justify-center rounded-[5px] text-[10px] font-bold tabular-nums transition-colors",
                failed
                  ? "bg-ae-warning text-black"
                  : kind === "retake"
                    ? "bg-ae-danger text-white"
                    : kind === "ok"
                      ? "bg-ae-success text-black"
                      : kind === "checking"
                        ? "animate-pulse bg-ae-blue text-white"
                        : done
                          ? "bg-ae-blue text-white"
                          : "bg-white/12 text-white/55",
                current && "ring-2 ring-white ring-offset-1 ring-offset-black",
              )}
            >
              {failed ? (
                <CloudOff className="size-3" aria-hidden />
              ) : kind === "retake" ? (
                <RotateCcw className="size-3" strokeWidth={3.5} aria-hidden />
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
