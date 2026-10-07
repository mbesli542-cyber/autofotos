"use client";

import { FlaskConical, LockKeyhole } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { TextField } from "@/components/ui/FormField";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { APP_INFO, BRAND } from "@/config/brand";
import { useAuthState } from "@/hooks/use-auth";
import { getAppServices, getBackendMode } from "@/lib/app-services";
import { DEMO_CREDENTIALS } from "@/lib/data/mock/demo-auth-service";

export function LoginScreen() {
  const router = useRouter();
  const auth = useAuthState();
  const isDemo = getBackendMode() === "demo";
  const [email, setEmail] = useState<string>(isDemo ? DEMO_CREDENTIALS.email : "");
  const [password, setPassword] = useState<string>(isDemo ? DEMO_CREDENTIALS.password : "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (auth.status === "authenticated") router.replace("/fahrzeuge");
  }, [auth.status, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Bitte geben Sie E-Mail und Passwort ein.");
      return;
    }
    setSubmitting(true);
    const result = await getAppServices().authStore.signIn(email, password);
    setSubmitting(false);
    if (result.ok) router.replace("/fahrzeuge");
    else setError(result.message);
  }

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden">
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[55vh] opacity-80"
        style={{
          background:
            "radial-gradient(60% 55% at 50% 0%, rgb(10 123 255 / 0.22) 0%, rgb(10 123 255 / 0.06) 45%, transparent 75%)",
        }}
        aria-hidden
      />
      <main className="pt-safe relative mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-5 py-10">
        <div className="mb-10 flex flex-col items-center text-center">
          <BrandLogo size="lg" />
          <p className="mt-3 text-sm font-medium tracking-[0.18em] text-ae-muted uppercase">Photo</p>
        </div>

        <form
          onSubmit={handleSubmit}
          noValidate
          className="rounded-2xl border border-ae-border bg-ae-surface p-5 shadow-[var(--shadow-card)]"
          aria-describedby={error ? "login-error" : undefined}
        >
          <h1 className="mb-1 text-xl font-semibold">Anmelden</h1>
          <p className="mb-5 text-sm text-ae-muted">Interner Zugang für Mitarbeitende.</p>

          {isDemo && (
            <div className="mb-5 flex gap-2.5 rounded-xl border border-ae-warning/30 bg-ae-warning/8 p-3 text-sm">
              <FlaskConical className="mt-0.5 size-4 shrink-0 text-ae-warning" aria-hidden />
              <p className="text-ae-muted">
                <span className="font-semibold text-ae-warning">Demo-Modus:</span> Supabase ist nicht
                konfiguriert. Beliebige Zugangsdaten funktionieren, Daten bleiben nur auf diesem Gerät.
              </p>
            </div>
          )}

          <div className="flex flex-col gap-4">
            <TextField
              id="email"
              label="E-Mail"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <TextField
              id="password"
              label="Passwort"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>

          {error && (
            <p id="login-error" role="alert" className="mt-4 text-sm text-ae-danger">
              {error}
            </p>
          )}

          <LoadingButton
            type="submit"
            size="lg"
            fullWidth
            className="mt-6"
            loading={submitting}
            loadingText="Anmeldung läuft…"
            icon={<LockKeyhole className="size-4" aria-hidden />}
          >
            Anmelden
          </LoadingButton>
        </form>

        <p className="mt-8 text-center text-xs text-ae-subtle">
          {APP_INFO.name} · {BRAND.companyName}
        </p>
      </main>
    </div>
  );
}
