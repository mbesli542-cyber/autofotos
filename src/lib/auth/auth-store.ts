/**
 * External store for the auth state (consumed with useSyncExternalStore).
 * Starts resolving the current user on first subscription.
 */
import type { AuthService, AuthUser, SignInResult } from "@/lib/data/types";

export type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; user: AuthUser }
  | { status: "unauthenticated" };

export const LOADING_AUTH_STATE: AuthState = { status: "loading" };

export class AuthStore {
  private state: AuthState = LOADING_AUTH_STATE;
  private started = false;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly auth: AuthService) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    this.start();
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): AuthState => this.state;

  getServerSnapshot = (): AuthState => LOADING_AUTH_STATE;

  async signIn(email: string, password: string): Promise<SignInResult> {
    const result = await this.auth.signIn(email, password);
    if (result.ok) this.setUser(result.user);
    return result;
  }

  async signOut(): Promise<void> {
    try {
      await this.auth.signOut();
    } finally {
      this.setUser(null);
    }
  }

  private start(): void {
    if (this.started) return;
    this.started = true;
    this.auth
      .getCurrentUser()
      .then((user) => this.setUser(user))
      .catch(() => this.setUser(null));
    this.auth.subscribe((user) => this.setUser(user));
  }

  private setUser(user: AuthUser | null): void {
    const next: AuthState = user ? { status: "authenticated", user } : { status: "unauthenticated" };
    const current = this.state;
    if (
      current.status === next.status &&
      (current.status !== "authenticated" ||
        (next.status === "authenticated" && current.user.id === next.user.id))
    ) {
      return;
    }
    this.state = next;
    for (const listener of this.listeners) listener();
  }
}
