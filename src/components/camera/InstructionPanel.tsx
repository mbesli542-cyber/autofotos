import { Lightbulb, Smartphone } from "lucide-react";

/**
 * Semi-transparent instruction box at the bottom of the preview: what to
 * photograph and the button that opens "Tipps für perfekte Fotos" again.
 * `rotate`: an exterior shot in portrait – the box also says to turn the
 * phone (the header's status line only asks briefly). The shot's photo hint
 * (`tip`, shot template) is NOT in the box: it is in the header's status line
 * (CheckBanner.tsx), so the box stays short and never reaches the framing
 * guide – also in the narrow portrait viewfinder of small phones.
 */
export function InstructionPanel({
  instruction,
  rotate = false,
  onShowTips,
}: {
  instruction: string;
  rotate?: boolean;
  onShowTips?: () => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-col items-center gap-2">
      <div className="flex max-w-md items-center gap-1 rounded-xl border border-white/10 bg-[rgb(30_33_38/0.78)] py-1 pr-1 pl-4 text-white backdrop-blur-md">
        <p className="min-w-0 flex-1 py-1.5 text-center text-[13px] leading-snug font-medium" aria-live="polite">
          {instruction}
          {rotate && (
            <span className="mt-0.5 flex items-center justify-center gap-1.5 text-[12px] font-semibold text-ae-warning">
              <Smartphone className="size-3.5 rotate-90" aria-hidden />
              Smartphone quer halten
            </span>
          )}
        </p>
        {onShowTips && (
          <button
            type="button"
            onClick={onShowTips}
            className="pointer-events-auto flex size-11 shrink-0 items-center justify-center rounded-lg text-white/80 hover:bg-white/10"
            aria-label="Tipps für perfekte Fotos"
          >
            <Lightbulb className="size-5" aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
