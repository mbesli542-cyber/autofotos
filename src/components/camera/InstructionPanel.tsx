import { Lightbulb, Smartphone } from "lucide-react";

/**
 * Semi-transparent instruction box over the preview: what to photograph and
 * the button that opens "Tipps für perfekte Fotos" again. `rotate`: an
 * exterior shot in portrait – the box also says to turn the phone (the
 * header's status line only asks briefly). `notice`: a short warning (e.g.
 * "Alle Pflichtfotos sind aufgenommen. …") next to the box. The shot's photo
 * hint (`tip`, shot template) is NOT in the box: it is in the header's status
 * line (CheckBanner.tsx).
 *
 * The camera puts the box on the stage where the framing guide leaves room
 * (`placement`, `edge`: the CSS offset of the viewfinder's edge) and as wide
 * as the stage allows – a narrow portrait viewfinder would wrap the text into
 * many lines: above the guide for exterior shots (their guides leave the top
 * third free), below it for interior and detail shots. Compact in short
 * viewports (landscape), where the guide fills the viewfinder's height.
 */
export function InstructionPanel({
  instruction,
  rotate = false,
  notice = null,
  onShowTips,
  placement,
  edge,
}: {
  instruction: string;
  rotate?: boolean;
  notice?: string | null;
  onShowTips?: () => void;
  placement: "top" | "bottom";
  /** CSS length of the box's distance to the stage's top (placement "top") or bottom edge. */
  edge: string;
}) {
  const noticePill = notice && (
    <p className="max-w-sm rounded-xl bg-ae-warning px-3 py-1.5 text-center text-xs font-semibold text-black" role="status">
      {notice}
    </p>
  );
  return (
    <div
      className="pointer-events-none absolute inset-x-3 flex flex-col items-center gap-2 short:gap-1.5"
      style={placement === "top" ? { top: edge } : { bottom: edge }}
      data-placement={placement}
    >
      {placement === "bottom" && noticePill}
      <div className="flex max-w-md items-center gap-1 rounded-xl border border-white/10 bg-[rgb(30_33_38/0.78)] py-1 pr-1 pl-4 text-white backdrop-blur-md short:py-0.5 short:pl-3">
        <p
          className="min-w-0 flex-1 py-1.5 text-center text-[13px] leading-snug font-medium short:py-1 short:text-[12px] short:leading-tight"
          aria-live="polite"
        >
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
            // Short viewports: 36 px, its tap area 44 px (an invisible extension).
            className="pointer-events-auto relative flex size-11 shrink-0 items-center justify-center rounded-lg text-white/80 after:absolute after:-inset-1 after:content-[''] hover:bg-white/10 short:size-9"
            aria-label="Tipps für perfekte Fotos"
          >
            <Lightbulb className="size-5" aria-hidden />
          </button>
        )}
      </div>
      {placement === "top" && noticePill}
    </div>
  );
}
