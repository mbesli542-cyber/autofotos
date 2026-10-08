import { Camera, Check, CloudOff, ImageOff } from "lucide-react";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { formatSlotNumber, type PhotoSlot } from "@/lib/photos/photo-slots";

export type PhotoVariant = "original" | "processed";

/**
 * Image for a slot. "processed" returns ONLY a real processed version –
 * never the original as a stand-in (null → "Noch nicht bearbeitet").
 */
export function getSlotImageUrl(slot: PhotoSlot, variant: PhotoVariant): string | null {
  if (variant === "processed") return slot.pending ? null : (slot.photo?.urls.processed ?? null);
  if (slot.pending?.thumbnailUrl) return slot.pending.thumbnailUrl;
  if (!slot.photo) return null;
  return slot.photo.urls.thumbnail;
}

/** 4:3 tile with shot number, capture state and checkmark. */
export function ShotThumbnail({
  slot,
  variant = "original",
  active = false,
  className,
}: {
  slot: PhotoSlot;
  variant?: PhotoVariant;
  active?: boolean;
  className?: string;
}) {
  const url = getSlotImageUrl(slot, variant);
  const pendingStatus = slot.pending?.status;
  const captured = Boolean(slot.photo) || Boolean(slot.pending);

  return (
    <div
      className={cn(
        "relative aspect-[4/3] w-full overflow-hidden rounded-xl bg-ae-surface-3",
        !captured && "border border-dashed border-ae-border-strong bg-ae-surface-2",
        active && "ring-2 ring-ae-blue ring-offset-2 ring-offset-ae-bg",
        className,
      )}
    >
      {url ? (
        <img src={url} alt="" className="size-full object-cover" loading="lazy" decoding="async" />
      ) : variant === "processed" && captured ? (
        <div className="flex size-full flex-col items-center justify-center gap-1 px-2 text-center text-ae-subtle">
          <ImageOff className="size-5" aria-hidden />
          <span className="text-[11px] leading-tight font-medium">Noch nicht bearbeitet</span>
        </div>
      ) : (
        <div className="flex size-full items-center justify-center text-ae-subtle">
          <Camera className="size-6" aria-hidden />
        </div>
      )}

      <span className="absolute top-1.5 left-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-bold text-white tabular-nums backdrop-blur-sm">
        {formatSlotNumber(slot)}
      </span>

      {pendingStatus === "failed" ? (
        <span
          className="absolute right-1.5 bottom-1.5 flex size-6 items-center justify-center rounded-full bg-ae-warning text-black"
          title="Nicht gespeichert"
        >
          <CloudOff className="size-3.5" aria-hidden />
        </span>
      ) : pendingStatus ? (
        <span
          className="absolute right-1.5 bottom-1.5 flex size-6 items-center justify-center rounded-full bg-black/70 text-white"
          title="Wird gespeichert"
        >
          <Spinner className="size-3.5" />
        </span>
      ) : slot.photo && (variant === "original" || url) ? (
        <span className="absolute right-1.5 bottom-1.5 flex size-6 items-center justify-center rounded-full bg-ae-blue text-white shadow">
          <Check className="size-3.5" strokeWidth={3} aria-hidden />
        </span>
      ) : null}
    </div>
  );
}
