import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Activity } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const backendMode = vi.hoisted(() => ({ current: "demo" as "demo" | "supabase" }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("@/lib/camera/image-utils", () => ({
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));
vi.mock("@/lib/app-services", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/app-services")>()),
  getBackendMode: () => backendMode.current,
}));

import { installDialogPolyfill } from "@/components/ui/dialog-test-polyfill";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import { MockDataProvider } from "@/lib/data/mock/mock-data-provider";
import { MoreScreen } from "./MoreScreen";

function renderMore() {
  const ui = (mode: "visible" | "hidden" = "visible") => (
    <ToastProvider>
      <Activity mode={mode}>
        <MoreScreen />
      </Activity>
    </ToastProvider>
  );
  const view = render(ui());
  const confirmDialog = () => document.querySelector<HTMLDialogElement>("dialog[aria-labelledby='confirm-title']");
  const openReset = () => fireEvent.click(screen.getByRole("button", { name: /Demo-Daten zurücksetzen/ }));
  const confirm = () => {
    const dialog = confirmDialog();
    if (!dialog) throw new Error("confirm dialog missing");
    fireEvent.click(within(dialog).getByRole("button", { name: "Zurücksetzen" }));
  };
  return { ...view, ui, confirmDialog, openReset, confirm };
}

function demoData(): MockDataProvider {
  const { data } = getAppServices().backend;
  if (!(data instanceof MockDataProvider)) throw new Error("tests run in demo mode");
  return data;
}

beforeAll(() => {
  installDialogPolyfill();
});

beforeEach(() => {
  backendMode.current = "demo";
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("<MoreScreen /> – 'Demo-Daten zurücksetzen'", () => {
  it("shows a German error and keeps the dialog usable when the reset fails", async () => {
    vi.spyOn(demoData(), "resetDemoData").mockRejectedValue(new DOMException("Quota", "QuotaExceededError"));
    const unhandled: unknown[] = [];
    const track = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", track);
    try {
      const { openReset, confirm, confirmDialog } = renderMore();
      openReset();
      await act(async () => confirm());
      await act(() => new Promise((resolve) => setTimeout(resolve, 30)));

      expect(screen.getByRole("alert")).toHaveTextContent("Es ist ein Fehler aufgetreten. Bitte versuchen Sie es erneut.");
      expect(unhandled).toEqual([]);
      expect(confirmDialog()?.open).toBe(true);
      const dialog = confirmDialog();
      if (!dialog) throw new Error("confirm dialog missing");
      expect(within(dialog).getByRole("button", { name: "Zurücksetzen" })).toBeEnabled();
    } finally {
      process.off("unhandledRejection", track);
    }
  });

  it("also clears captures still waiting in the upload queue", async () => {
    const { uploads } = getAppServices();
    // Waits forever: its vehicle does not exist in the demo data.
    await uploads.enqueue({
      vehicleId: "00000000-0000-4000-8000-000000000000",
      shotKey: "rear",
      shotOrder: 7,
      title: "Hinten",
      file: new Blob(["jpeg"], { type: "image/jpeg" }),
      takenAt: new Date().toISOString(),
    });
    await waitFor(() => expect(uploads.getSnapshot().items[0]?.status).toBe("failed"));
    const clear = vi.spyOn(uploads, "clear");
    const reset = vi.spyOn(demoData(), "resetDemoData");

    const { openReset, confirm, confirmDialog } = renderMore();
    openReset();
    await act(async () => confirm());

    await waitFor(() => expect(screen.getByText("Demo-Daten wurden zurückgesetzt.")).toBeInTheDocument());
    expect(uploads.getSnapshot().items).toEqual([]);
    expect(clear).toHaveBeenCalledOnce();
    expect(clear.mock.invocationCallOrder[0]).toBeLessThan(reset.mock.invocationCallOrder[0] ?? 0);
    expect(confirmDialog()?.open).toBe(false);
  });

  it("closes the question when the route is hidden and does not bring it back", async () => {
    const { openReset, confirmDialog, rerender, ui } = renderMore();
    openReset();
    expect(confirmDialog()?.open).toBe(true);

    rerender(ui("hidden")); // e.g. browser back
    expect(confirmDialog()?.open).toBe(false);

    rerender(ui("visible"));
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(confirmDialog()?.open).toBe(false);
  });
});

describe("<MoreScreen /> – texts without technical backend names", () => {
  it("describes demo mode in plain German", () => {
    renderMore();
    expect(screen.getByText("Fahrzeuge und Fotos werden nur lokal in diesem Browser gespeichert.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/supabase/i);
  });

  it("describes the online mode in plain German", () => {
    backendMode.current = "supabase";
    renderMore();
    expect(screen.getByText("Angemeldet · Daten werden online gespeichert")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/supabase/i);
  });
});
