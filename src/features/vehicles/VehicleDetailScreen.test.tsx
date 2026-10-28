/**
 * Vehicle page with the real app services (demo backend in memory, real
 * upload queue). Next.js keeps routes mounted with React <Activity>; search
 * params (?ansicht=bearbeitet) do not create a new page instance.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Activity, type ReactNode } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import type { ProcessingWarning } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import { completeCapture, markProcessedIfDone, savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { VehicleDetailScreen } from "./VehicleDetailScreen";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
// Thumbnails need a real image decoder (canvas), which jsdom does not have.
vi.mock("@/lib/camera/image-utils", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/camera/image-utils")>()),
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));

const PROCESSED = "processed";

beforeAll(() => {
  // jsdom has no modal <dialog> API.
  const dialog = HTMLDialogElement.prototype;
  dialog.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  dialog.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
  // Processed results are recognisable by their size.
  let n = 0;
  URL.createObjectURL = (blob: Blob | MediaSource) =>
    `blob:${(blob as Blob).size === PROCESSED.length ? "processed" : "original"}-${++n}`;
  URL.revokeObjectURL = () => {};
});

const REMOVED: ProcessingWarning = {
  code: "background_object_removed",
  message:
    "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
};

/**
 * All required photos (processed) plus "Zusatzfoto 1"; the vehicle is
 * "Bearbeitet". `warnings`: the processor's hints per shot key.
 */
async function processedVehicleWithExtra(warnings: Record<string, ProcessingWarning[]> = {}) {
  const { backend, template } = getAppServices();
  const data = backend.data;
  const vehicle = await data.createVehicle({
    manufacturer: "Porsche",
    model: "Macan",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  const shots = [
    ...template.shots.map((shot) => ({ shotKey: shot.key, shotOrder: shot.order, title: shot.title })),
    { shotKey: "extra_01", shotOrder: 101, title: "Zusatzfoto 1" },
  ];
  for (const shot of shots) {
    await savePhotoAndSyncStatus(data, template, {
      photoId: crypto.randomUUID(),
      vehicleId: vehicle.id,
      ...shot,
      file: new Blob(["orig"], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: new Date().toISOString(),
    });
  }
  await completeCapture(data, template, vehicle.id);
  for (const photo of await data.listPhotos(vehicle.id)) {
    if (photo.shotKey.startsWith("extra_")) continue;
    await data.saveProcessedPhoto({
      photo,
      preset: "autoexperten_standard",
      file: new Blob([PROCESSED], { type: "image/jpeg" }),
      warnings: warnings[photo.shotKey] ?? [],
    });
  }
  await markProcessedIfDone(data, template, vehicle.id);
  return vehicle;
}

function page(vehicleId: string, initialVariant: "original" | "processed", mode: "visible" | "hidden" = "visible") {
  return (
    <ToastProvider>
      <Activity mode={mode}>
        <VehicleDetailScreen vehicleId={vehicleId} initialVariant={initialVariant} />
      </Activity>
    </ToastProvider>
  );
}

const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const selectedVersion = () =>
  screen.getAllByRole("radio").find((option) => option.getAttribute("aria-checked") === "true")?.textContent;
const heroImage = (container: HTMLElement) => container.querySelector("section img")?.getAttribute("src");

describe("<VehicleDetailScreen />", () => {
  it("retakes an additional photo with one picked file – never with the camera on shot 01", async () => {
    const vehicle = await processedVehicleWithExtra();
    const { backend } = getAppServices();
    const before = await backend.data.listPhotos(vehicle.id);
    const view = render(page(vehicle.id, "original"));

    fireEvent.click(await screen.findByRole("button", { name: "+1 Zusatzfoto 1 – aufgenommen" }));
    const dialog = view.container.querySelector<HTMLElement>("dialog[aria-labelledby='photo-viewer-title']");
    if (!dialog) throw new Error("photo viewer not open");
    const retakeInput = view.container.querySelector<HTMLInputElement>('input[type="file"]:not([multiple])');
    if (!retakeInput) throw new Error("no single-file input");
    const opened = vi.fn();
    retakeInput.addEventListener("click", opened);
    fireEvent.click(within(dialog).getByRole("button", { name: "Foto wiederholen", hidden: true }));
    expect(router.push).not.toHaveBeenCalled();
    expect(opened).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.change(retakeInput, {
        target: { files: [new File(["new"], "zusatz.jpg", { type: "image/jpeg", lastModified: Date.parse("2020-01-01") })] },
      });
    });
    await waitFor(async () => {
      const extra = (await backend.data.listPhotos(vehicle.id)).find((photo) => photo.shotKey === "extra_01");
      expect(extra?.id).not.toBe(before.find((photo) => photo.shotKey === "extra_01")?.id);
    });

    const after = await backend.data.listPhotos(vehicle.id);
    const shot01 = (photos: typeof after) => photos.find((photo) => photo.shotKey === "front_left_45");
    // Shot 01 and its processed version are untouched; the vehicle stays "Bearbeitet".
    expect(shot01(after)?.id).toBe(shot01(before)?.id);
    expect(shot01(after)?.processedStoragePath).not.toBeNull();
    expect(after.filter((photo) => photo.shotKey.startsWith("extra_")).map((photo) => photo.title)).toEqual(["Zusatzfoto 1"]);
    expect((await backend.data.getVehicle(vehicle.id))?.status).toBe("processed");
  });

  it("shows the processed photos for 'Ergebnisse ansehen' although the page was kept from an earlier visit", async () => {
    const vehicle = await processedVehicleWithExtra();
    // 1) /fahrzeuge/X  2) "Fotos bearbeiten" (the vehicle page is hidden, not unmounted)
    const view = render(page(vehicle.id, "original"));
    await screen.findByRole("heading", { level: 2, name: "Porsche Macan" });
    expect(selectedVersion()).toBe("Original");
    view.rerender(page(vehicle.id, "original", "hidden"));
    await wait(10);

    // 3) "Ergebnisse ansehen" → /fahrzeuge/X?ansicht=bearbeitet: the same page, new prop.
    view.rerender(page(vehicle.id, "processed"));
    await wait(50);
    expect(selectedVersion()).toBe("Bearbeitet");
    expect(heroImage(view.container)).toMatch(/^blob:processed-/);

    // The toggle still works while the page is shown …
    fireEvent.click(screen.getByRole("radio", { name: "Original" }));
    expect(selectedVersion()).toBe("Original");
    expect(heroImage(view.container)).toMatch(/^blob:original-/);

    // … and "Ergebnisse ansehen" after another run shows the processed photos again.
    view.rerender(page(vehicle.id, "processed", "hidden"));
    await wait(10);
    view.rerender(page(vehicle.id, "processed"));
    await wait(50);
    expect(selectedVersion()).toBe("Bearbeitet");
  });

  it("keeps the status on the photo readable with an opaque chip", async () => {
    const vehicle = await processedVehicleWithExtra();
    const view = render(page(vehicle.id, "processed"));
    await screen.findByRole("heading", { level: 2, name: "Porsche Macan" });
    const hero = view.container.querySelector("section");
    if (!hero) throw new Error("no hero");
    const badge = within(hero).getByText("Bearbeitet");
    expect(badge).toHaveClass("bg-ae-bg", "absolute");
    expect(badge).not.toHaveClass("bg-ae-blue/15");
  });

  it("points the employee to the processed photos the processor asks to check", async () => {
    const vehicle = await processedVehicleWithExtra({ front: [REMOVED] });
    const view = render(page(vehicle.id, "processed"));
    expect(await screen.findByText("Bitte ein bearbeitetes Foto prüfen – Hinweis in der Großansicht.")).toBeInTheDocument();
    const card = screen.getByRole("button", { name: "02 Vorne – bearbeitet, bitte prüfen" });
    expect(within(card).getByTitle("Bitte prüfen")).toBeInTheDocument();

    fireEvent.click(card);
    const dialog = view.container.querySelector<HTMLElement>("dialog[aria-labelledby='photo-viewer-title']");
    if (!dialog) throw new Error("photo viewer not open");
    expect(within(dialog).getByText(REMOVED.message)).toBeInTheDocument();

    // The originals view stays as it was.
    fireEvent.click(within(dialog).getByRole("button", { name: "Großansicht schließen", hidden: true }));
    fireEvent.click(screen.getByRole("radio", { name: "Original" }));
    expect(screen.queryByText(/bearbeitetes Foto prüfen/)).not.toBeInTheDocument();
  });

  it("says inside the large view why a photo could not be deleted (a toast would be hidden behind it)", async () => {
    const vehicle = await processedVehicleWithExtra();
    const { backend } = getAppServices();
    const archive = vi.spyOn(backend.data, "archivePhoto").mockRejectedValueOnce(new AppError("network"));
    try {
      const view = render(page(vehicle.id, "original"));
      fireEvent.click(await screen.findByRole("button", { name: /^02 Vorne – / }));
      const dialog = view.container.querySelector<HTMLElement>("dialog[aria-labelledby='photo-viewer-title']");
      if (!dialog) throw new Error("photo viewer not open");
      fireEvent.click(within(dialog).getByRole("button", { name: "Foto löschen", hidden: true }));
      await act(async () => {
        fireEvent.click(within(dialog).getByRole("button", { name: "Löschen", hidden: true }));
      });
      await waitFor(() =>
        expect(within(dialog).getByRole("alert", { hidden: true })).toHaveTextContent(
          "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.",
        ),
      );
      // Only there – not (also) as a toast behind the modal viewer.
      expect(screen.getAllByRole("alert", { hidden: true })).toHaveLength(1);
      expect(within(dialog).getByText("Foto wirklich löschen?")).toBeInTheDocument();

      // The second attempt deletes it.
      await act(async () => {
        fireEvent.click(within(dialog).getByRole("button", { name: "Löschen", hidden: true }));
      });
      await waitFor(() => expect(within(dialog).queryByText("Foto wirklich löschen?")).not.toBeInTheDocument());
      expect(archive).toHaveBeenCalledTimes(2);
      expect((await backend.data.listPhotos(vehicle.id)).some((photo) => photo.shotKey === "front")).toBe(false);
    } finally {
      archive.mockRestore();
    }
  });

  it("offers 'Erneut versuchen' only for captures a retry can save and points refused files to 'Verwerfen'", async () => {
    const vehicle = await processedVehicleWithExtra();
    const { backend, uploads } = getAppServices();
    const save = vi.spyOn(backend.data, "savePhoto").mockRejectedValue(new AppError("unsupported_file"));
    const refused = await uploads.enqueue({
      vehicleId: vehicle.id,
      shotKey: "front",
      shotOrder: 2,
      title: "Vorne",
      file: new Blob(["avif"], { type: "image/avif" }),
      takenAt: new Date().toISOString(),
    });
    try {
      await waitFor(() =>
        expect(uploads.getSnapshot().items.find((item) => item.id === refused.id)).toMatchObject({
          status: "failed",
          retryable: false,
        }),
      );
      render(page(vehicle.id, "original"));
      expect(
        await screen.findByText(
          "1 Foto kann nicht gespeichert werden. Bitte das Foto antippen und in der Großansicht „Verwerfen“ wählen.",
        ),
      ).toBeInTheDocument();
      // A retry could never save it – there is nothing to retry.
      expect(screen.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
      expect(screen.queryByText(/konnten noch nicht gespeichert werden/)).not.toBeInTheDocument();
    } finally {
      save.mockRestore();
      await uploads.discard(refused.id);
    }
  });
});
