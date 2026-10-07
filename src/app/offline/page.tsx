import { WifiOff } from "lucide-react";
import type { Metadata } from "next";
import { BrandLogo } from "@/components/brand/BrandLogo";

export const metadata: Metadata = { title: "Offline" };

/** Fallback served by the service worker when a page cannot be loaded. */
export default function OfflinePage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col items-center justify-center gap-5 px-6 text-center">
      <BrandLogo size="lg" />
      <WifiOff className="size-10 text-ae-muted" aria-hidden />
      <div>
        <h1 className="text-lg font-semibold">Keine Verbindung</h1>
        <p className="mt-2 text-sm text-ae-muted">
          Verbindung fehlgeschlagen. Bereits aufgenommene Fotos bleiben auf diesem Gerät gespeichert
          und werden hochgeladen, sobald die Verbindung zurück ist.
        </p>
      </div>
      {/* Full page load on purpose: retries the network instead of client navigation. */}
      {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
      <a
        href="/fahrzeuge"
        className="inline-flex h-11 items-center rounded-xl bg-ae-blue px-5 font-semibold text-white"
      >
        Erneut versuchen
      </a>
    </main>
  );
}
