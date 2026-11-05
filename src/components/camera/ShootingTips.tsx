"use client";

import { Camera, Droplets, Lightbulb, MoveHorizontal, Scan, Smartphone, Tag, Warehouse, X, type LucideIcon } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { SHOOTING_TIPS, type ShootingTipIcon } from "@/lib/shots/shooting-guide";

const ICONS: Record<ShootingTipIcon, LucideIcon> = {
  hall: Warehouse,
  clean: Droplets,
  space: MoveHorizontal,
  frame: Scan,
  plate: Tag,
  landscape: Smartphone,
  original: Camera,
};

/** The photo tips (src/lib/shots/shooting-guide.ts) as a compact list. */
export function ShootingTipsList({ className }: { className?: string }) {
  return (
    <ul className={cn("flex flex-col gap-2.5", className)}>
      {SHOOTING_TIPS.map((tip) => {
        const Icon = ICONS[tip.icon];
        return (
          <li key={tip.id} className="flex gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-ae-blue-soft text-ae-blue">
              <Icon className={cn("size-[18px]", tip.icon === "landscape" && "rotate-90")} aria-hidden />
            </span>
            <span className="min-w-0 text-sm leading-snug">
              <span className="block font-semibold text-ae-text">{tip.title}</span>
              <span className="text-ae-muted">{tip.text}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * "Tipps für perfekte Fotos" over the camera: opens by itself once for a new
 * vehicle and from the instruction box. One tap closes it; `onClose` gets
 * whether it should still open by itself for the next new vehicle.
 */
export function ShootingTipsSheet({
  showAutoChoice,
  onClose,
}: {
  /** Opened by itself: offers "nicht mehr automatisch zeigen". */
  showAutoChoice: boolean;
  onClose: (autoShow: boolean) => void;
}) {
  const titleId = useId();
  const [autoShow, setAutoShow] = useState(true);
  const close = () => onClose(autoShow);
  return (
    <div
      className="fixed inset-0 z-[60] flex animate-fade-in items-end justify-center bg-black/65 sm:items-center sm:p-4 short:items-center short:p-2"
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        className="pb-safe flex max-h-full w-full max-w-lg flex-col rounded-t-2xl border border-white/10 bg-ae-surface text-ae-text shadow-xl sm:rounded-2xl short:max-w-2xl short:rounded-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex items-start gap-2 px-4 pt-4">
          <Lightbulb className="mt-0.5 size-5 shrink-0 text-ae-blue" aria-hidden />
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="font-semibold">
              Tipps für perfekte Fotos
            </h2>
            <p className="text-xs text-ae-muted">So wirken die Fotos im Showroom am besten.</p>
          </div>
          <button
            type="button"
            onClick={close}
            className="-mt-1.5 -mr-1.5 flex size-11 items-center justify-center rounded-lg text-ae-muted hover:bg-white/10"
            aria-label="Tipps schließen"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <ShootingTipsList className="short:grid short:grid-cols-2 short:gap-x-4" />
        </div>
        <div className="flex flex-col gap-2 border-t border-ae-border px-4 py-3 short:flex-row-reverse short:items-center">
          <Button size="lg" fullWidth className="short:h-11 short:w-auto short:min-w-56" onClick={close}>
            Verstanden – los geht’s
          </Button>
          {showAutoChoice && (
            <label className="flex min-h-11 flex-1 cursor-pointer items-center gap-3 text-sm text-ae-muted">
              <input
                type="checkbox"
                className="size-4 shrink-0 accent-ae-blue"
                checked={!autoShow}
                onChange={(event) => setAutoShow(!event.target.checked)}
              />
              Bei neuen Fahrzeugen nicht mehr automatisch zeigen
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
