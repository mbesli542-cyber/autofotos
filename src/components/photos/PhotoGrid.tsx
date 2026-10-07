import type { PhotoSlot } from "@/lib/photos/photo-slots";
import { PhotoReviewCard } from "./PhotoReviewCard";
import type { PhotoVariant } from "./ShotThumbnail";

/** Photo grid in template order – 3 columns on phones. */
export function PhotoGrid({
  slots,
  variant = "original",
  onSelect,
  label,
}: {
  slots: readonly PhotoSlot[];
  variant?: PhotoVariant;
  onSelect: (slot: PhotoSlot) => void;
  label: string;
}) {
  return (
    <ul className="grid grid-cols-3 gap-x-2.5 gap-y-3.5 sm:grid-cols-4 lg:grid-cols-5" aria-label={label}>
      {slots.map((slot) => (
        <li key={slot.key}>
          <PhotoReviewCard slot={slot} variant={variant} onSelect={onSelect} />
        </li>
      ))}
    </ul>
  );
}
