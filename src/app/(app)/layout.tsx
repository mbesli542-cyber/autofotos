import type { ReactNode } from "react";
import { AuthGate } from "@/components/auth/AuthGate";

/**
 * Auth-gated area: the session is checked on the client, so these segments
 * have no meaningful static shell (it is the splash screen) and are allowed
 * to block on navigation.
 */
export const instant = false;

/** Everything inside (app) requires a signed-in user. */
export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return <AuthGate>{children}</AuthGate>;
}
