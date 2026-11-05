import { Smartphone } from "lucide-react";

/**
 * Exterior shot while the camera delivers a portrait picture: a clear prompt
 * in the middle of the viewfinder to turn the phone (in portrait the car is
 * small – the processing then asks for a retake). Never blocks the shutter.
 */
export function RotatePhonePrompt() {
  return (
    <div className="pointer-events-none absolute inset-x-4 top-1/2 z-10 flex -translate-y-1/2 justify-center" role="status">
      <div className="flex max-w-[17rem] flex-col items-center gap-2 rounded-2xl border border-white/15 bg-black/70 px-5 py-4 text-center text-white shadow-lg backdrop-blur-md">
        <Smartphone className="size-10 text-ae-blue motion-safe:animate-turn-phone motion-reduce:rotate-90" aria-hidden />
        <p className="text-base leading-tight font-semibold">Bitte Smartphone quer halten</p>
        <p className="text-xs leading-snug text-white/75">
          Außenfotos nur im Querformat – dann passt das ganze Fahrzeug mit Rand ins Bild.
        </p>
        <p className="text-[11px] leading-snug text-white/55">
          Dreht sich das Bild nicht? Ausrichtungssperre des Smartphones ausschalten.
        </p>
      </div>
    </div>
  );
}
