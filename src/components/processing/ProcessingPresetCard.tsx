import { Check } from "lucide-react";
import { LOGO_ASSETS } from "@/config/brand";
import { cn } from "@/lib/cn";
import type { ProcessingPreset } from "@/lib/processing/presets";

/** Selectable style card with a small visual impression of the preset. */
export function ProcessingPresetCard({
  preset,
  selected,
  disabled,
  onSelect,
}: {
  preset: ProcessingPreset;
  selected: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  const isShowroom = preset.background === "showroom";
  const lightScene = preset.uiPreview.textTone === "dark";

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        "flex w-full gap-3.5 rounded-2xl border p-3 text-left transition-colors disabled:opacity-60",
        selected
          ? "border-ae-blue bg-ae-blue-soft shadow-[0_0_0_1px_var(--color-ae-blue)]"
          : "border-ae-border bg-ae-surface hover:border-ae-border-strong",
      )}
    >
      <div
        className="relative aspect-[4/3] w-28 shrink-0 overflow-hidden rounded-xl border border-black/20"
        style={{ background: preset.uiPreview.background }}
        aria-hidden
      >
        {isShowroom && (
          <>
            <span className="absolute top-[12%] bottom-[45%] left-[10%] w-[3px] rounded-full bg-ae-blue shadow-[0_0_8px_2px_rgb(10_123_255/0.8)]" />
            <span className="absolute top-[12%] right-[10%] bottom-[45%] w-[3px] rounded-full bg-ae-blue shadow-[0_0_8px_2px_rgb(10_123_255/0.8)]" />
            {/* Official logo file – never redrawn as text. */}
            <img
              src={lightScene ? LOGO_ASSETS.onLight : LOGO_ASSETS.onDark}
              alt=""
              width={LOGO_ASSETS.width}
              height={LOGO_ASSETS.height}
              decoding="async"
              draggable={false}
              className="absolute top-[13%] left-1/2 h-auto w-[52%] -translate-x-1/2 select-none"
            />
          </>
        )}
        <img
          src="/overlays/front-left-45.svg"
          alt=""
          className="absolute inset-x-0 bottom-[4%] w-full"
          style={{ filter: lightScene ? "invert(1) brightness(0.25)" : undefined, opacity: 0.85 }}
        />
      </div>
      <div className="min-w-0 flex-1 py-0.5">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold">{preset.name}</h3>
          <span
            className={cn(
              "flex size-5 shrink-0 items-center justify-center rounded-full border",
              selected ? "border-ae-blue bg-ae-blue text-white" : "border-ae-border-strong",
            )}
            aria-hidden
          >
            {selected && <Check className="size-3.5" strokeWidth={3} />}
          </span>
        </div>
        <p className="mt-1 text-sm leading-snug text-ae-muted">{preset.description}</p>
      </div>
    </button>
  );
}
