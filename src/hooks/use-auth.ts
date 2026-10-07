"use client";

import { useSyncExternalStore } from "react";
import { getAppServices } from "@/lib/app-services";
import { LOADING_AUTH_STATE, type AuthState } from "@/lib/auth/auth-store";

const noopUnsubscribe = () => {};

function subscribe(listener: () => void) {
  if (typeof window === "undefined") return noopUnsubscribe;
  return getAppServices().authStore.subscribe(listener);
}

function getSnapshot(): AuthState {
  return getAppServices().authStore.getSnapshot();
}

function getServerSnapshot(): AuthState {
  return LOADING_AUTH_STATE;
}

export function useAuthState(): AuthState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
