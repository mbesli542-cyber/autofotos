import { cn } from "@/lib/cn";
import { formatSlotNumber, type PhotoSlot } from "@/lib/photos/photo-slots";
import { SHOT_CHECK_LABELS, type ShotCheckState } from "@/lib/processing/shot-check";
import { processedNeedsCheck, ShotThumbnail, type PhotoVariant } from "./ShotThumbnail";

function describeState(slot: PhotoSlot, variant: PhotoVariant, check: ShotCheckState | undefined): string {
  if (slot.pending?.status === "failed") return "nicht gespeichert";
  if (slot.pending) return check?.kind === "checking" ? "wird gespeichert und geprüft" : "wird gespeichert";
  if (check?.kind === "retake") return `bitte neu aufnehmen: ${check.reason}`;
  if (check?.kind === "checking") return "wird geprüft";
  if (processedNeedsCheck(slot, variant) || (check?.kind === "ok" && check.hint)) return SHOT_CHECK_LABELS.hint;
  if (check?.kind === "ok") return "geprüft, passt";
  if (check?.kind === "saved") return "gespeichert";
  return slot.photo ? "aufgenommen" : "fehlt";
}

/** Grid card: thumbnail + number + shot name; opens the large preview. */
export function PhotoReviewCard({
  slot,
  variant,
  check,
  onSelect,
}: {
  slot: PhotoSlot;
  variant?: PhotoVariant;
  /** Result of the automatic check (shot-check.ts). */
  check?: ShotCheckState;
  onSelect: (slot: PhotoSlot) => void;
}) {
  const missing = !slot.photo && !slot.pending;
  return (
    <button
      type="button"
      onClick={() => onSelect(slot)}
      className="group flex w-full flex-col gap-1.5 rounded-xl text-left"
      aria-label={`${formatSlotNumber(slot)} ${slot.title} – ${describeState(slot, variant ?? "original", check)}`}
    >
      <ShotThumbnail
        slot={slot}
        variant={variant}
        check={check}
        className="transition-transform duration-150 group-active:scale-[0.97]"
      />
      <span
        className={cn(
          "line-clamp-2 px-0.5 text-[12px] leading-tight font-medium",
          missing ? "text-ae-subtle" : "text-ae-text",
        )}
      >
        {slot.title}
      </span>
    </button>
  );
}
