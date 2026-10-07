import { cn } from "@/lib/cn";
import type { PhotoVariant } from "./ShotThumbnail";

const OPTIONS: Array<{ value: PhotoVariant; label: string }> = [
  { value: "original", label: "Original" },
  { value: "processed", label: "Bearbeitet" },
];

/** Segmented control to switch between original and processed versions. */
export function PhotoVersionToggle({
  value,
  onChange,
  processedAvailable = true,
  className,
}: {
  value: PhotoVariant;
  onChange: (value: PhotoVariant) => void;
  processedAvailable?: boolean;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Version"
      className={cn("inline-flex rounded-xl border border-ae-border bg-ae-surface-2 p-1", className)}
    >
      {OPTIONS.map((option) => {
        const selected = value === option.value;
        const disabled = option.value === "processed" && !processedAvailable;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-8 rounded-lg px-3.5 text-sm font-semibold transition-colors disabled:opacity-40",
              selected ? "bg-ae-blue text-white" : "text-ae-muted hover:text-ae-text",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
