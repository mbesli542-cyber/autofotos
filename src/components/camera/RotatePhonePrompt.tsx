"use client";

import { Smartphone } from "lucide-react";
import { useEffect, useState } from "react";

/** How long the large prompt stays before only the instruction box says it (ms). */
export const ROTATE_PROMPT_MS = 3_500;

/**
 * Exterior shot while the camera delivers a portrait picture: a large prompt
 * in the middle of the viewfinder to turn the phone – in portrait the car is
 * small and the processing may ask for a retake. It goes away after a few
 * seconds or on a tap, so the car can still be framed (e.g. when the phone
 * does not turn); the instruction box keeps saying it. Stays mounted while
 * the shots follow each other in portrait (`paused` during the preview), so
 * it is shown large once, not before every shot. Never blocks the shutter.
 */
export function RotatePhonePrompt({ paused = false }: { paused?: boolean }) {
  const [shown, setShown] = useState(true);

  useEffect(() => {
    if (!shown) return;
    const timer = setTimeout(() => setShown(false), ROTATE_PROMPT_MS);
    return () => clearTimeout(timer);
  }, [shown]);

  if (!shown || paused) return null;
  return (
    <div className="pointer-events-none absolute inset-x-3 top-3 bottom-20 z-10 flex items-center justify-center">
      <button
        type="button"
        onClick={() => setShown(false)}
        className="pointer-events-auto flex max-w-[15rem] animate-fade-in flex-col items-center gap-1.5 rounded-2xl border border-white/15 bg-black/70 px-4 py-3 text-center text-white shadow-lg backdrop-blur-md"
        aria-label="Bitte Smartphone quer halten – Hinweis ausblenden"
      >
        <Smartphone className="size-8 text-ae-blue motion-safe:animate-turn-phone motion-reduce:rotate-90" aria-hidden />
        <span className="text-[15px] leading-tight font-semibold">Bitte Smartphone quer halten</span>
        <span className="text-xs leading-snug text-white/75">Außenfotos nur im Querformat – das ganze Auto mit Rand.</span>
        <span className="text-[11px] leading-snug text-white/55">
          Bild dreht sich nicht? iPhone: Ausrichtungssperre aus · Android: „Automatisch drehen“ an.
        </span>
      </button>
    </div>
  );
}
