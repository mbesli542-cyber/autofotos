import type { ReactNode } from "react";
import { BottomNavigation } from "@/components/layout/BottomNavigation";

/** Main app shell with the bottom tab navigation. */
export default function TabsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh pb-[calc(68px+env(safe-area-inset-bottom))]">
      {children}
      <BottomNavigation />
    </div>
  );
}
