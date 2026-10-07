"use client";

import { useEffect } from "react";

/**
 * Registers the service worker in production builds (installable PWA,
 * offline fallback). In development any old worker is removed so it never
 * serves stale code.
 */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") {
      void navigator.serviceWorker
        .getRegistrations()
        .then((registrations) => Promise.all(registrations.map((r) => r.unregister())));
      return;
    }
    void navigator.serviceWorker
      .register("/sw.js", { scope: "/", updateViaCache: "none" })
      .catch(() => {
        // Not critical – the app works without a service worker.
      });
  }, []);
  return null;
}
