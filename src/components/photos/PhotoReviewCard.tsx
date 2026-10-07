import { cn } from "@/lib/cn";
import { formatSlotNumber, type PhotoSlot } from "@/lib/photos/photo-slots";
import { ShotThumbnail, type PhotoVariant } from "./ShotThumbnail";

function describeState(slot: PhotoSlot): string {
  if (slot.pending?.status === "failed") return "nicht gespeichert";
  if (slot.pending) return "wird gespeichert";
  return slot.photo ? "aufgenommen" : "fehlt";
}

/** Grid card: thumbnail + number + shot name; opens the large preview. */
export function PhotoReviewCard({
  slot,
  variant,
  onSelect,
}: {
  slot: PhotoSlot;
  variant?: PhotoVariant;
  onSelect: (slot: PhotoSlot) => void;
}) {
  const missing = !slot.photo && !slot.pending;
  return (
    <button
      type="button"
      onClick={() => onSelect(slot)}
      className="group flex w-full flex-col gap-1.5 rounded-xl text-left"
      aria-label={`${formatSlotNumber(slot)} ${slot.title} – ${describeState(slot)}`}
    >
      <ShotThumbnail
        slot={slot}
        variant={variant}
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
