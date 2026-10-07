"use client";

import { Camera, CarFront, Ellipsis, Pencil, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  isActive: (pathname: string) => boolean;
}

const isEditPath = (pathname: string) =>
  pathname.startsWith("/bearbeiten") || /^\/fahrzeuge\/[^/]+\/bearbeiten/.test(pathname);

const NAV_ITEMS: NavItem[] = [
  {
    href: "/fahrzeuge",
    label: "Fahrzeuge",
    icon: CarFront,
    isActive: (p) => p.startsWith("/fahrzeuge") && !isEditPath(p),
  },
  { href: "/kamera", label: "Kamera", icon: Camera, isActive: (p) => p.startsWith("/kamera") },
  { href: "/bearbeiten", label: "Bearbeiten", icon: Pencil, isActive: isEditPath },
  { href: "/mehr", label: "Mehr", icon: Ellipsis, isActive: (p) => p.startsWith("/mehr") },
];

export const BOTTOM_NAV_HEIGHT = 68;

export function BottomNavigation() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Hauptnavigation"
      className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-ae-border/80 bg-ae-bg/92 backdrop-blur-md"
    >
      <ul className="mx-auto grid h-[68px] max-w-lg grid-cols-4">
        {NAV_ITEMS.map(({ href, label, icon: Icon, isActive }) => {
          const active = isActive(pathname);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-full flex-col items-center justify-center gap-1 text-[11px] font-semibold transition-colors",
                  active ? "text-ae-blue" : "text-ae-muted hover:text-ae-text",
                )}
              >
                <span
                  className={cn(
                    "flex h-7 w-12 items-center justify-center rounded-full transition-colors",
                    active && "bg-ae-blue-soft",
                  )}
                >
                  <Icon className="size-[22px]" strokeWidth={active ? 2.3 : 1.9} aria-hidden />
                </span>
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
