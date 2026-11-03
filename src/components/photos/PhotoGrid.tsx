import type { PhotoSlot } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { PhotoReviewCard } from "./PhotoReviewCard";
import type { PhotoVariant } from "./ShotThumbnail";

/** Photo grid in template order – 3 columns on phones. */
export function PhotoGrid({
  slots,
  variant = "original",
  checks,
  onSelect,
  label,
}: {
  slots: readonly PhotoSlot[];
  variant?: PhotoVariant;
  /** Results of the automatic check by shot key. */
  checks?: ReadonlyMap<string, ShotCheckState>;
  onSelect: (slot: PhotoSlot) => void;
  label: string;
}) {
  return (
    <ul className="grid grid-cols-3 gap-x-2.5 gap-y-3.5 sm:grid-cols-4 lg:grid-cols-5" aria-label={label}>
      {slots.map((slot) => (
        <li key={slot.key}>
          <PhotoReviewCard slot={slot} variant={variant} check={checks?.get(slot.key)} onSelect={onSelect} />
        </li>
      ))}
    </ul>
  );
}
