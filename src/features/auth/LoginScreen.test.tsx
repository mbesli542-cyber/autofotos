/**
 * The /login route stays mounted (Activity) while the app is used: after
 * "Abmelden" it must not offer the previous user's credentials to the next
 * person on a shared device. Real auth store (demo auth service).
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Activity } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAppServices } from "@/lib/app-services";
import { DEMO_CREDENTIALS } from "@/lib/data/mock/demo-auth-service";
import type { BackendMode } from "@/lib/data/types";
import { LoginScreen } from "./LoginScreen";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace }) }));

const backend = vi.hoisted(() => ({ mode: "demo" as BackendMode }));
vi.mock("@/lib/app-services", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/app-services")>()),
  getBackendMode: () => backend.mode,
}));

function loginRoute(visible: boolean) {
  return (
    <Activity mode={visible ? "visible" : "hidden"}>
      <LoginScreen />
    </Activity>
  );
}

const emailField = () => screen.getByLabelText(/E-Mail/) as HTMLInputElement;
const passwordField = () => screen.getByLabelText(/Passwort/) as HTMLInputElement;

function typeCredentials() {
  fireEvent.change(emailField(), { target: { value: "anna.meier@autoexperten-rn.de" } });
  fireEvent.change(passwordField(), { target: { value: "Werkstatt#2026" } });
}

async function signIn() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
  });
  await waitFor(() => expect(replace).toHaveBeenCalledWith("/fahrzeuge"));
}

beforeEach(async () => {
  replace.mockClear();
  backend.mode = "demo";
  await getAppServices().authStore.signOut();
});

describe("<LoginScreen />", () => {
  it("does not keep a real account's e-mail and password after signing in and signing out", async () => {
    backend.mode = "supabase";
    const { rerender } = render(loginRoute(true));
    expect(emailField().value).toBe("");
    typeCredentials();
    await signIn();
    expect(emailField().value).toBe("");
    expect(passwordField().value).toBe("");
    // Until the app is shown the button stays busy: no second submit, no "Bitte geben Sie …".
    const busy = screen.getByRole("button", { name: "Anmeldung läuft…" });
    expect(busy).toBeDisabled();
    fireEvent.submit(busy.closest("form")!);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    rerender(loginRoute(false)); // signed in: the app is shown, /login stays mounted
    await act(() => getAppServices().authStore.signOut()); // "Abmelden"
    rerender(loginRoute(true));
    expect(emailField().value).toBe("");
    expect(passwordField().value).toBe("");
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });

  it("resets typed credentials when the route is hidden", async () => {
    backend.mode = "supabase";
    const { rerender } = render(loginRoute(true));
    typeCredentials();
    rerender(loginRoute(false));
    rerender(loginRoute(true));
    expect(emailField().value).toBe("");
    expect(passwordField().value).toBe("");
  });

  it("demo mode: goes back to the prefilled demo credentials and names no backend", async () => {
    const { rerender } = render(loginRoute(true));
    const notice = screen.getByText("Demo-Modus:").closest("p");
    expect(notice).toHaveTextContent(
      "Demo-Modus: Beliebige Zugangsdaten funktionieren, Daten bleiben nur auf diesem Gerät.",
    );
    expect(document.body).not.toHaveTextContent(/supabase/i);

    typeCredentials();
    await signIn();
    expect(emailField().value).toBe(DEMO_CREDENTIALS.email);
    expect(passwordField().value).toBe(DEMO_CREDENTIALS.password);

    rerender(loginRoute(false));
    await act(() => getAppServices().authStore.signOut());
    rerender(loginRoute(true));
    expect(passwordField().value).toBe(DEMO_CREDENTIALS.password);
    expect(passwordField().value).not.toBe("Werkstatt#2026");
  });
});
