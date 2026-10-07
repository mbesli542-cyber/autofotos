/**
 * Demo-mode authentication. Any e-mail + password is accepted; the session is
 * kept in localStorage. Only used when Supabase is not configured.
 */
import type { AuthService, AuthUser, SignInResult } from "@/lib/data/types";

const STORAGE_KEY = "ae-photo.demo-session";
export const DEMO_CREDENTIALS = {
  email: "demo@autoexperten-rn.de",
  password: "demo",
} as const;

function readSession(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "id" in parsed) {
      const { id, email } = parsed as { id: unknown; email: unknown };
      if (typeof id === "string") {
        return { id, email: typeof email === "string" ? email : null };
      }
    }
  } catch {
    // ignore corrupt or blocked storage
  }
  return null;
}

export class DemoAuthService implements AuthService {
  private listeners = new Set<(user: AuthUser | null) => void>();

  async getCurrentUser(): Promise<AuthUser | null> {
    return readSession();
  }

  async signIn(email: string, password: string): Promise<SignInResult> {
    const trimmed = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      return { ok: false, message: "Bitte geben Sie eine gültige E-Mail-Adresse ein." };
    }
    if (password.length === 0) {
      return { ok: false, message: "Bitte geben Sie ein Passwort ein." };
    }
    const user: AuthUser = { id: "demo-user", email: trimmed };
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
    } catch {
      // storage blocked – session lives until reload
    }
    this.emit(user);
    return { ok: true, user };
  }

  async signOut(): Promise<void> {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    this.emit(null);
  }

  subscribe(listener: (user: AuthUser | null) => void): () => void {
    this.listeners.add(listener);
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) listener(readSession());
    };
    window.addEventListener("storage", onStorage);
    return () => {
      this.listeners.delete(listener);
      window.removeEventListener("storage", onStorage);
    };
  }

  private emit(user: AuthUser | null): void {
    for (const listener of this.listeners) listener(user);
  }
}
