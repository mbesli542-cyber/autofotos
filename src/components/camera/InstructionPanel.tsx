import { Smartphone } from "lucide-react";

/** Semi-transparent instruction box at the bottom of the preview. */
export function InstructionPanel({
  instruction,
  showRotateHint,
}: {
  instruction: string;
  showRotateHint: boolean;
}) {
  return (
    <div className="pointer-events-none absolute inset-x-3 bottom-3 flex flex-col items-center gap-2">
      {showRotateHint && (
        <p className="flex items-center gap-1.5 rounded-full bg-black/55 px-3 py-1 text-[11px] font-medium text-white/90 backdrop-blur">
          <Smartphone className="size-3.5 rotate-90" aria-hidden />
          Tipp: Für Außenaufnahmen Smartphone quer halten
        </p>
      )}
      <p
        className="max-w-md rounded-xl border border-white/10 bg-[rgb(30_33_38/0.78)] px-4 py-2.5 text-center text-[13px] leading-snug font-medium text-white backdrop-blur-md"
        aria-live="polite"
      >
        {instruction}
      </p>
    </div>
  );
}
