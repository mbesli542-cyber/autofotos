import type { SupabaseClient, User } from "@supabase/supabase-js";
import type { AuthService, AuthUser, SignInResult } from "@/lib/data/types";
import { isNetworkError, USER_ERROR_MESSAGES } from "@/lib/errors";

function toAuthUser(user: User | null | undefined): AuthUser | null {
  return user ? { id: user.id, email: user.email ?? null } : null;
}

export class SupabaseAuthService implements AuthService {
  constructor(private readonly client: SupabaseClient) {}

  async getCurrentUser(): Promise<AuthUser | null> {
    // Local session read (fast). Authorisation is enforced server-side by RLS.
    const { data } = await this.client.auth.getSession();
    return toAuthUser(data.session?.user);
  }

  async signIn(email: string, password: string): Promise<SignInResult> {
    try {
      const { data, error } = await this.client.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (error) {
        if (isNetworkError(error) || error.status === 0) {
          return { ok: false, message: USER_ERROR_MESSAGES.network };
        }
        if (error.status === 429) {
          return {
            ok: false,
            message: "Zu viele Anmeldeversuche. Bitte warten Sie einen Moment.",
          };
        }
        return { ok: false, message: "E-Mail oder Passwort ist falsch." };
      }
      const user = toAuthUser(data.user);
      return user
        ? { ok: true, user }
        : { ok: false, message: "Anmeldung fehlgeschlagen. Bitte versuchen Sie es erneut." };
    } catch {
      return { ok: false, message: USER_ERROR_MESSAGES.network };
    }
  }

  async signOut(): Promise<void> {
    await this.client.auth.signOut();
  }

  subscribe(listener: (user: AuthUser | null) => void): () => void {
    const { data } = this.client.auth.onAuthStateChange((_event, session) => {
      listener(toAuthUser(session?.user));
    });
    return () => data.subscription.unsubscribe();
  }
}
