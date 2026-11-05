import { Lightbulb } from "lucide-react";

/**
 * Semi-transparent instruction box at the bottom of the preview: what to
 * photograph, the shot's photo hint (`tip`, shot template) and the button
 * that opens "Tipps für perfekte Fotos" again.
 */
export function InstructionPanel({
  instruction,
  tip = null,
  onShowTips,
}: {
  instruction: string;
  tip?: string | null;
  onShowTips?: () => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-col items-center gap-2">
      <div className="flex max-w-md items-center gap-1 rounded-xl border border-white/10 bg-[rgb(30_33_38/0.78)] py-1 pr-1 pl-4 text-white backdrop-blur-md">
        <p className="min-w-0 flex-1 py-1.5 text-center text-[13px] leading-snug font-medium" aria-live="polite">
          {instruction}
          {tip && <span className="mt-0.5 block text-[12px] font-normal text-white/70">{tip}</span>}
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
