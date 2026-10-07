import { ChevronLeft, ChevronRight, ImageUp, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * Bottom controls (thumb zone): previous shot thumbnail, large shutter,
 * "Weiter". Every control has a visible text label – not only an icon.
 */
export function CaptureControls({
  previousThumbnailUrl,
  previousTitle,
  canGoBack,
  onBack,
  onShutter,
  shutterLabel,
  shutterDisabled,
  busy,
  nextLabel,
  onNext,
  onUpload,
}: {
  previousThumbnailUrl: string | null;
  previousTitle: string | null;
  canGoBack: boolean;
  onBack: () => void;
  onShutter: () => void;
  shutterLabel: string;
  shutterDisabled: boolean;
  busy: boolean;
  nextLabel: "Weiter" | "Übersicht";
  onNext: () => void;
  onUpload: () => void;
}) {
  return (
    <div className="pb-safe flex w-full flex-col items-center gap-2 px-4 pt-3 landscape:h-full landscape:justify-center landscape:px-3">
      <div className="grid w-full max-w-md grid-cols-3 items-center landscape:max-w-none landscape:grid-cols-1 landscape:gap-5 short:gap-2.5">
        {/* Previous shot */}
        <div className="flex justify-start landscape:order-3 landscape:justify-center">
          <button
            type="button"
            onClick={onBack}
            disabled={!canGoBack}
            className="flex flex-col items-center gap-1 text-[11px] font-medium text-white/80 disabled:opacity-35"
            aria-label={previousTitle ? `Zurück zu: ${previousTitle}` : "Zurück"}
          >
            <span className="relative flex size-14 items-center justify-center overflow-hidden rounded-xl border border-white/25 bg-white/8 short:size-11">
              {previousThumbnailUrl ? (
                <img src={previousThumbnailUrl} alt="" className="size-full object-cover" />
              ) : (
                <ChevronLeft className="size-6" aria-hidden />
              )}
            </span>
            Zurück
          </button>
        </div>

        {/* Shutter */}
        <div className="flex justify-center landscape:order-2">
          <button
            type="button"
            onClick={onShutter}
            disabled={shutterDisabled || busy}
            className="group flex flex-col items-center gap-1.5 text-[11px] font-semibold text-white disabled:opacity-40"
            aria-label={shutterLabel}
          >
            <span className="flex size-[78px] items-center justify-center rounded-full border-[4px] border-white/90 transition-transform group-active:scale-95 short:size-16">
              <span
                className={cn(
                  "rounded-full transition-all",
                  busy ? "size-[50px] bg-white/70 short:size-10" : "size-[62px] bg-white short:size-[50px]",
                )}
              />
            </span>
            {shutterLabel}
          </button>
        </div>

        {/* Next */}
        <div className="flex justify-end landscape:order-1 landscape:justify-center">
          <button
            type="button"
            onClick={onNext}
            className="flex h-11 items-center gap-1 rounded-xl bg-white/12 px-4 text-sm font-semibold text-white backdrop-blur hover:bg-white/20"
          >
            {nextLabel === "Übersicht" && <LayoutGrid className="size-4" aria-hidden />}
            {nextLabel}
            {nextLabel === "Weiter" && <ChevronRight className="size-4" aria-hidden />}
          </button>
        </div>
      </div>

      <button
        type="button"
        onClick={onUpload}
        className="flex h-9 items-center gap-1.5 rounded-lg px-3 text-[13px] font-medium text-white/70 hover:text-white landscape:mt-2 short:mt-0 short:h-8"
      >
        <ImageUp className="size-4" aria-hidden />
        Foto hochladen
      </button>
    </div>
  );
}
