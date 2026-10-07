import { SearchX } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { BrandLogo } from "@/components/brand/BrandLogo";

export const metadata: Metadata = { title: "Seite nicht gefunden" };

/** German 404 page (also used for the developer routes when they are disabled). */
export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col items-center justify-center gap-5 px-6 text-center">
      <BrandLogo size="lg" />
      <SearchX className="size-10 text-ae-muted" aria-hidden />
      <div>
        <h1 className="text-lg font-semibold">Seite nicht gefunden</h1>
        <p className="mt-2 text-sm text-ae-muted">
          Diese Seite gibt es nicht oder sie ist nicht mehr verfügbar.
        </p>
      </div>
      <Link
        href="/fahrzeuge"
        className="inline-flex h-11 items-center rounded-xl bg-ae-blue px-5 font-semibold text-white"
      >
        Zu den Fahrzeugen
      </Link>
    </main>
  );
}
