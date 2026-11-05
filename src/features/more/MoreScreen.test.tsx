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
import { readJobJournal, recordJob } from "@/lib/processing/job-journal";
import { readStoredRun, storeRun } from "@/lib/processing/stored-runs";
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
  const openReset = () =>
    fireEvent.click(screen.getByRole("button", { name: /Alle Daten auf diesem Gerät löschen/ }));
  const dialogEl = () => {
    const dialog = confirmDialog();
    if (!dialog) throw new Error("confirm dialog missing");
    return dialog;
  };
  const typeConfirmation = (text: string) =>
    fireEvent.change(within(dialogEl()).getByLabelText(/Zum Bestätigen/), { target: { value: text } });
  const confirmButton = () => within(dialogEl()).getByRole("button", { name: "Alles löschen" });
  const confirm = () => {
    typeConfirmation("LÖSCHEN");
    fireEvent.click(confirmButton());
  };
  return { ...view, ui, confirmDialog, openReset, confirm, typeConfirmation, confirmButton };
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

describe("<MoreScreen /> – 'Alle Daten auf diesem Gerät löschen'", () => {
  it("deletes only after LÖSCHEN was typed (any case), and an empty field again for the next question", async () => {
    const clear = vi.spyOn(demoData(), "clearAllData");
    const { openReset, typeConfirmation, confirmButton, confirmDialog } = renderMore();
    openReset();
    expect(confirmDialog()?.open).toBe(true);
    expect(confirmButton()).toBeDisabled();
    typeConfirmation("LÖSCH");
    expect(confirmButton()).toBeDisabled();
    typeConfirmation(" löschen ");
    expect(confirmButton()).toBeEnabled();

    fireEvent.click(within(confirmDialog() as HTMLDialogElement).getByRole("button", { name: "Abbrechen" }));
    expect(clear).not.toHaveBeenCalled();
    openReset();
    expect(within(confirmDialog() as HTMLDialogElement).getByLabelText(/Zum Bestätigen/)).toHaveValue("");
    expect(confirmButton()).toBeDisabled();
  });

  it("shows a German error and keeps the dialog usable when deleting fails", async () => {
    vi.spyOn(demoData(), "clearAllData").mockRejectedValue(new DOMException("Blocked", "UnknownError"));
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
      expect(within(dialog).getByRole("button", { name: "Alles löschen" })).toBeEnabled();
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
    const reset = vi.spyOn(demoData(), "clearAllData");

    const { openReset, confirm, confirmDialog } = renderMore();
    openReset();
    await act(async () => confirm());

    await waitFor(() => expect(screen.getByText("Alle Daten auf diesem Gerät wurden gelöscht.")).toBeInTheDocument());
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

describe("<MoreScreen /> – 'Abmelden'", () => {
  it("leaves nothing of the user's processing on the device for the next person", async () => {
    const { authStore } = getAppServices();
    await act(() => authStore.signIn("anna.meier@autoexperten-rn.de", "Werkstatt#2026"));
    const startedAt = new Date().toISOString();
    storeRun({
      userId: "demo-user",
      vehicleId: "veh_1",
      preset: "autoexperten_standard",
      reprocess: false,
      startedAt,
      items: [
        {
          photoId: "p_front",
          shotKey: "front",
          title: "Vorne",
          shotOrder: 2,
          treatment: "showroom",
          status: "open",
          error: null,
          errorCode: null,
          warnings: [],
        },
      ],
    });
    recordJob({
      userId: "demo-user",
      vehicleId: "veh_1",
      photoId: "p_front",
      jobId: "0123456789abcdef0123456789abcdef",
      preset: "autoexperten_standard",
      startedAt,
      shotKey: "front",
      shotOrder: 2,
      title: "Vorne",
      takenAt: startedAt,
    });
    renderMore();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Abmelden" })));
    // A run or check of this user is neither continued after a reload nor by the next person.
    expect(readStoredRun("veh_1")).toBeNull();
    expect(readJobJournal()).toEqual([]);
    expect(authStore.getSnapshot().status).toBe("unauthenticated");
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
