"use client";

import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ui/Toast";
import { ServiceWorkerRegistrar } from "./ServiceWorkerRegistrar";
import { StoragePersistence } from "./StoragePersistence";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      {children}
      <ServiceWorkerRegistrar />
      <StoragePersistence />
    </ToastProvider>
  );
}
