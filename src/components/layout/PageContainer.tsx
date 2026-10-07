import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function PageContainer({ children, className }: { children: ReactNode; className?: string }) {
  return <main className={cn("mx-auto w-full max-w-5xl px-4 pt-4 pb-6", className)}>{children}</main>;
}

/** Sticky action area above the bottom navigation – keeps CTAs in thumb reach. */
export function StickyActions({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "sticky bottom-[calc(68px+env(safe-area-inset-bottom))] z-20 -mx-4 mt-6 border-t border-ae-border/70 bg-ae-bg/90 px-4 py-3 backdrop-blur-md sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:px-0 sm:backdrop-blur-none",
        className,
      )}
    >
      {children}
    </div>
  );
}
