"use client";

import { Camera, Droplets, Lightbulb, MoveHorizontal, Scan, Smartphone, Tag, Warehouse, X, type LucideIcon } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef } from "react";
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
 * "Tipps für perfekte Fotos" over the camera, as a modal native <dialog>
 * (focus stays inside, Esc closes): opens by itself once per device for a new
 * vehicle and from the instruction box (then with the current shot's hint
 * first, `shotTip`). One tap closes it – "Verstanden", the
 * X, Esc or a tap outside (the sheet never fills the whole screen).
 */
export function ShootingTipsSheet({
  onClose,
  shotTip = null,
}: {
  onClose: () => void;
  /** The current shot's photo hint (shot template `tip`), shown first. */
  shotTip?: { title: string; tip: string } | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  // The primary action gets the focus: Enter closes the tips.
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", ""); // no modal API (older browsers, jsdom)
    }
    dialog.querySelector<HTMLElement>("[data-initial-focus]")?.focus();
  }, []);

  // Closed when the camera route gets hidden (routes stay mounted): a modal
  // left open would block the page that is shown.
  useLayoutEffect(() => () => ref.current?.close?.(), []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // The panel fills the dialog's box: a click on the dialog itself is on the backdrop.
        if (event.target === event.currentTarget) onClose();
      }}
      className="m-0 mt-auto max-h-none w-full max-w-none bg-transparent p-0 text-ae-text backdrop:bg-black/65 sm:m-auto sm:max-w-lg short:m-auto short:max-w-2xl"
    >
      <div className="pb-safe flex max-h-[calc(100dvh-3.5rem)] animate-slide-up flex-col rounded-t-2xl border border-white/10 bg-ae-surface shadow-xl sm:rounded-2xl short:max-h-[calc(100dvh-1rem)] short:rounded-2xl">
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
            onClick={onClose}
            className="-mt-1.5 -mr-1.5 flex size-11 items-center justify-center rounded-lg text-ae-muted hover:bg-white/10"
            aria-label="Tipps schließen"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {shotTip && (
            <p className="mb-3 rounded-xl border border-ae-blue/30 bg-ae-blue-soft px-3 py-2 text-sm leading-snug">
              <span className="block font-semibold text-ae-text">Für dieses Foto ({shotTip.title})</span>
              <span className="text-ae-muted">{shotTip.tip}</span>
            </p>
          )}
          <ShootingTipsList className="short:grid short:grid-cols-2 short:gap-x-4" />
        </div>
        <div className="flex border-t border-ae-border px-4 py-3 short:justify-end">
          <Button size="lg" fullWidth className="short:h-11 short:w-auto short:min-w-56" onClick={onClose} data-initial-focus="">
            Verstanden – los geht’s
          </Button>
        </div>
      </div>
    </dialog>
  );
}
