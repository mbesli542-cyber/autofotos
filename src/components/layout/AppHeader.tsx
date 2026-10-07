import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { DemoModeBadge } from "./DemoModeBadge";

/**
 * Sticky top bar. Without a title it shows the AutoExperten logo
 * (main screens); with `backHref` it shows a back button (sub pages).
 */
export function AppHeader({
  title,
  backHref,
  backLabel = "Zurück",
  actions,
}: {
  title?: string;
  backHref?: string;
  backLabel?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="pt-safe sticky top-0 z-30 border-b border-ae-border/70 bg-ae-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4">
        {backHref && (
          <Link
            href={backHref}
            className="-ml-2 flex size-10 shrink-0 items-center justify-center rounded-xl text-ae-text hover:bg-ae-surface-2"
            aria-label={backLabel}
          >
            <ChevronLeft className="size-6" aria-hidden />
          </Link>
        )}
        {title ? (
          <h1 className="min-w-0 truncate text-[17px] font-semibold">{title}</h1>
        ) : (
          <BrandLogo size="md" />
        )}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <DemoModeBadge />
          {actions}
        </div>
      </div>
    </header>
  );
}
