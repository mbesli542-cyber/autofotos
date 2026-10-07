"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { Spinner } from "@/components/ui/Spinner";
import { useAuthState } from "@/hooks/use-auth";
import { getAppServices } from "@/lib/app-services";

/** Renders children only for signed-in users; otherwise redirects to /login. */
export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuthState();
  const router = useRouter();

  useEffect(() => {
    if (auth.status === "unauthenticated") router.replace("/login");
  }, [auth.status, router]);

  useEffect(() => {
    // Resume photo uploads that did not finish in a previous session.
    if (auth.status === "authenticated") void getAppServices().uploads.restore();
  }, [auth.status]);

  if (auth.status !== "authenticated") return <SplashScreen />;
  return children;
}

export function SplashScreen() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-6" role="status" aria-live="polite">
      <BrandLogo size="lg" />
      <Spinner className="size-6 text-ae-blue" />
      <span className="sr-only">Wird geladen…</span>
    </div>
  );
}
