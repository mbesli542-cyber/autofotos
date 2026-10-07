"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SplashScreen } from "@/components/auth/AuthGate";
import { useAuthState } from "@/hooks/use-auth";

/** "/" → /fahrzeuge when signed in, otherwise /login. */
export function RootRedirect() {
  const auth = useAuthState();
  const router = useRouter();

  useEffect(() => {
    if (auth.status === "authenticated") router.replace("/fahrzeuge");
    if (auth.status === "unauthenticated") router.replace("/login");
  }, [auth.status, router]);

  return <SplashScreen />;
}
