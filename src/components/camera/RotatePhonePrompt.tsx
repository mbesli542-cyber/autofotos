import { Smartphone } from "lucide-react";

/**
 * Exterior shot while the camera delivers a portrait picture: a clear prompt
 * in the viewfinder (above the instruction box) to turn the phone – in
 * portrait the car is small and the processing asks for a retake. Never
 * blocks the shutter.
 */
export function RotatePhonePrompt() {
  return (
    <div
      className="pointer-events-none absolute inset-x-3 top-3 bottom-20 z-10 flex items-center justify-center"
      role="status"
    >
      <div className="flex max-w-[15rem] flex-col items-center gap-1.5 rounded-2xl border border-white/15 bg-black/70 px-4 py-3 text-center text-white shadow-lg backdrop-blur-md">
        <Smartphone className="size-8 text-ae-blue motion-safe:animate-turn-phone motion-reduce:rotate-90" aria-hidden />
        <p className="text-[15px] leading-tight font-semibold">Bitte Smartphone quer halten</p>
        <p className="text-xs leading-snug text-white/75">Außenfotos nur im Querformat – das ganze Auto mit Rand.</p>
        <p className="text-[11px] leading-snug text-white/55">Bild dreht sich nicht? Ausrichtungssperre ausschalten.</p>
      </div>
    </div>
  );
}
