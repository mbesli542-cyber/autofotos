import { FlaskConical } from "lucide-react";
import { getBackendMode } from "@/lib/app-services";
import { cn } from "@/lib/cn";

/** Small indicator shown while the app runs without Supabase (local demo data). */
export function DemoModeBadge({ className }: { className?: string }) {
  if (getBackendMode() !== "demo") return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-ae-warning/35 bg-ae-warning/10 px-2 py-0.5 text-[11px] font-semibold text-ae-warning",
        className,
      )}
      title="Keine Supabase-Verbindung konfiguriert – Daten werden nur lokal in diesem Browser gespeichert."
    >
      <FlaskConical className="size-3" aria-hidden />
      Demo-Modus
    </span>
  );
}
