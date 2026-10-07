"use client";

import { Building2, Globe, LogOut, Phone, RotateCcw, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useToast } from "@/components/ui/Toast";
import { APP_INFO, BRAND } from "@/config/brand";
import { useAuthState } from "@/hooks/use-auth";
import { getAppServices, getBackendMode, getShotTemplate } from "@/lib/app-services";
import { MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import { getOrderedShots, SHOT_CATEGORY_LABELS } from "@/lib/shots/shot-template";

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-ae-border bg-ae-surface p-4" aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold text-ae-muted">{title}</h2>
      {children}
    </section>
  );
}

export function MoreScreen() {
  const router = useRouter();
  const toast = useToast();
  const auth = useAuthState();
  const isDemo = getBackendMode() === "demo";
  const template = getShotTemplate();
  const [confirmReset, setConfirmReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  async function handleSignOut() {
    await getAppServices().authStore.signOut();
    router.replace("/login");
  }

  async function handleResetDemo() {
    const { data } = getAppServices().backend;
    if (!(data instanceof MockDataProvider)) return;
    setResetting(true);
    try {
      await data.resetDemoData();
      toast.success("Demo-Daten wurden zurückgesetzt.");
      setConfirmReset(false);
    } finally {
      setResetting(false);
    }
  }

  return (
    <>
      <AppHeader title="Mehr" />
      <PageContainer className="flex max-w-2xl flex-col gap-4">
        <Card title="Konto">
          <div className="flex items-center gap-3">
            <span className="flex size-11 items-center justify-center rounded-full bg-ae-blue-soft text-ae-blue">
              <UserRound className="size-5" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">
                {auth.status === "authenticated" ? (auth.user.email ?? "Angemeldet") : "–"}
              </p>
              <p className="text-xs text-ae-muted">
                {isDemo ? "Demo-Modus · Daten nur auf diesem Gerät" : "Verbunden mit Supabase"}
              </p>
            </div>
          </div>
          <Button
            variant="secondary"
            fullWidth
            className="mt-4"
            onClick={handleSignOut}
            icon={<LogOut className="size-4" aria-hidden />}
          >
            Abmelden
          </Button>
        </Card>

        <Card title={`Fotostandard · ${template.name}`}>
          <ol className="divide-y divide-ae-border/60">
            {getOrderedShots(template).map((shot) => (
              <li key={shot.key} className="flex items-center gap-3 py-2 text-sm">
                <span className="w-6 text-xs font-bold text-ae-subtle tabular-nums">
                  {String(shot.order).padStart(2, "0")}
                </span>
                <span className="flex-1">{shot.title}</span>
                <span className="text-xs text-ae-muted">{SHOT_CATEGORY_LABELS[shot.category]}</span>
              </li>
            ))}
          </ol>
        </Card>

        <Card title="Unternehmen">
          <ul className="flex flex-col gap-1 text-sm">
            <li className="flex items-center gap-3 py-1.5">
              <Building2 className="size-4 text-ae-muted" aria-hidden />
              {BRAND.companyName}
            </li>
            <li>
              <a href={BRAND.website} target="_blank" rel="noreferrer" className="flex items-center gap-3 py-1.5 text-ae-blue">
                <Globe className="size-4" aria-hidden />
                {BRAND.websiteLabel}
              </a>
            </li>
            <li>
              <a href={BRAND.phoneHref} className="flex items-center gap-3 py-1.5 text-ae-blue">
                <Phone className="size-4" aria-hidden />
                {BRAND.phone}
              </a>
            </li>
          </ul>
        </Card>

        {isDemo && (
          <Card title="Demo-Modus">
            <p className="text-sm text-ae-muted">
              Supabase ist nicht konfiguriert. Fahrzeuge und Fotos werden nur lokal in diesem Browser
              gespeichert.
            </p>
            <Button
              variant="danger"
              fullWidth
              className="mt-4"
              onClick={() => setConfirmReset(true)}
              icon={<RotateCcw className="size-4" aria-hidden />}
            >
              Demo-Daten zurücksetzen
            </Button>
          </Card>
        )}

        <p className="py-2 text-center text-xs text-ae-subtle">
          {APP_INFO.name} · Version {APP_INFO.version}
        </p>
      </PageContainer>

      <ConfirmDialog
        open={confirmReset}
        title="Demo-Daten zurücksetzen?"
        description="Alle lokal erfassten Fahrzeuge und Fotos werden gelöscht und die Beispielfahrzeuge wiederhergestellt."
        confirmLabel="Zurücksetzen"
        busy={resetting}
        onConfirm={handleResetDemo}
        onCancel={() => setConfirmReset(false)}
      />
    </>
  );
}
