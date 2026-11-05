/**
 * "Fotos überprüfen" with the real app services (demo backend in memory,
 * real upload queue): retaking an additional photo and adding photos.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import { completeCapture, savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { PhotoReviewScreen } from "./PhotoReviewScreen";

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

beforeAll(() => {
  // jsdom has no modal <dialog> API.
  const dialog = HTMLDialogElement.prototype;
  dialog.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  dialog.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
  let n = 0;
  URL.createObjectURL = () => `blob:test-${++n}`;
  URL.revokeObjectURL = () => {};
});

/** A vehicle with all required photos plus "Zusatzfoto 1", capture completed. */
async function completedVehicleWithExtra(extraTakenAt = new Date().toISOString()) {
  const { backend, template } = getAppServices();
  const data = backend.data;
  const vehicle = await data.createVehicle({
    manufacturer: "Kia",
    model: "Ceed",
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
      file: new Blob(["jpeg"], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: shot.shotKey === "extra_01" ? extraTakenAt : new Date().toISOString(),
    });
  }
  await completeCapture(data, template, vehicle.id);
  return vehicle;
}

async function activeExtras(vehicleId: string) {
  const photos = await getAppServices().backend.data.listPhotos(vehicleId);
  return photos.filter((photo) => photo.shotKey.startsWith("extra_"));
}

function openViewer(container: HTMLElement): HTMLElement {
  const dialog = container.querySelector<HTMLElement>("dialog[aria-labelledby='photo-viewer-title']");
  if (!dialog) throw new Error("photo viewer not open");
  return dialog;
}

describe("<PhotoReviewScreen />", () => {
  it("replaces exactly the additional photo that is retaken (one picked file)", async () => {
    const vehicle = await completedVehicleWithExtra();
    const [before] = await activeExtras(vehicle.id);
    const view = render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "+1 Zusatzfoto 1 – aufgenommen" }));
    const retakeInput = view.container.querySelector<HTMLInputElement>('input[type="file"]:not([multiple])');
    if (!retakeInput) throw new Error("no single-file input");
    const opened = vi.fn();
    retakeInput.addEventListener("click", opened);
    fireEvent.click(within(openViewer(view.container)).getByRole("button", { name: "Foto wiederholen", hidden: true }));
    expect(opened).toHaveBeenCalledTimes(1);
    // Without HEIC: iOS then converts library photos to JPEG (see photo-file.ts).
    expect(retakeInput).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");
    expect(router.push).not.toHaveBeenCalled();

    // A file from the gallery, older than the photo it replaces.
    const picked = new File(["sharp"], "IMG_0815.jpg", { type: "image/jpeg", lastModified: Date.parse("2020-01-01") });
    await act(async () => {
      fireEvent.change(retakeInput, { target: { files: [picked] } });
    });
    expect(screen.getByText("Zusatzfoto wird gespeichert…")).toBeInTheDocument();

    await waitFor(async () => {
      const extras = await activeExtras(vehicle.id);
      expect(extras.map((photo) => [photo.shotKey, photo.shotOrder, photo.title])).toEqual([["extra_01", 101, "Zusatzfoto 1"]]);
      expect(extras[0]?.id).not.toBe(before?.id);
    });
  });

  it("replaces an additional photo that is dated ahead (a camera with a wrong clock) – with the real capture time", async () => {
    // Saved 10 minutes ago, dated a month ahead: it counts as taken 5 minutes after it
    // was saved (the database's rule, src/lib/photos/capture-order.ts).
    vi.useFakeTimers({ now: Date.now() - 10 * 60_000, toFake: ["Date"] });
    const vehicle = await completedVehicleWithExtra(new Date(Date.now() + 30 * 86_400_000).toISOString()).finally(() =>
      vi.useRealTimers(),
    );
    const [before] = await activeExtras(vehicle.id);
    const startedAt = Date.now();
    const view = render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "+1 Zusatzfoto 1 – aufgenommen" }));
    const retakeInput = view.container.querySelector<HTMLInputElement>('input[type="file"]:not([multiple])');
    if (!retakeInput) throw new Error("no single-file input");
    fireEvent.click(within(openViewer(view.container)).getByRole("button", { name: "Foto wiederholen", hidden: true }));
    await act(async () => {
      fireEvent.change(retakeInput, { target: { files: [new File(["sharp"], "retake.jpg", { type: "image/jpeg" })] } });
    });

    await waitFor(async () => {
      const extras = await activeExtras(vehicle.id);
      expect(extras.map((photo) => photo.shotKey)).toEqual(["extra_01"]);
      expect(extras[0]?.id).not.toBe(before?.id);
    });
    // "Aufgenommen am" shows when it was taken – not a date after the photo dated ahead.
    const [retake] = await activeExtras(vehicle.id);
    expect(Date.parse(retake?.takenAt ?? "")).toBeGreaterThanOrEqual(startedAt);
    expect(Date.parse(retake?.takenAt ?? "")).toBeLessThanOrEqual(Date.now());
    expect(getAppServices().uploads.getSnapshot().items.filter((item) => item.vehicleId === vehicle.id)).toEqual([]);
  });

  it("dates added photos by the file, but never in the future", async () => {
    const vehicle = await completedVehicleWithExtra();
    const view = render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    await screen.findByRole("button", { name: "+1 Zusatzfoto 1 – aufgenommen" });
    const addInput = view.container.querySelector<HTMLInputElement>('input[type="file"][multiple]');
    if (!addInput) throw new Error("no input for additional photos");
    const ahead = new File(["sd"], "DSC_0001.JPG", { type: "image/jpeg", lastModified: Date.now() + 30 * 86_400_000 });
    const older = new File(["jpeg"], "IMG_0002.jpg", { type: "image/jpeg", lastModified: Date.parse("2026-05-01T08:00:00.000Z") });
    await act(async () => {
      fireEvent.change(addInput, { target: { files: [ahead, older] } });
    });

    await waitFor(async () => expect((await activeExtras(vehicle.id)).map((photo) => photo.shotKey)).toEqual(["extra_01", "extra_02", "extra_03"]));
    const extras = await activeExtras(vehicle.id);
    expect(Date.parse(extras[1]?.takenAt ?? "")).toBeLessThanOrEqual(Date.now());
    expect(extras[2]?.takenAt).toBe("2026-05-01T08:00:00.000Z");
  });

  it("adds only photos in a supported format and size", async () => {
    const vehicle = await completedVehicleWithExtra();
    const view = render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    await screen.findByRole("button", { name: "+1 Zusatzfoto 1 – aufgenommen" });
    const addInput = view.container.querySelector<HTMLInputElement>('input[type="file"][multiple]');
    if (!addInput) throw new Error("no input for additional photos");
    expect(addInput).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");

    const avif = new File(["avif"], "IMG_0001.avif", { type: "image/avif" });
    const jpeg = new File(["jpeg"], "IMG_0002.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(addInput, { target: { files: [avif, jpeg] } });
    });
    expect(screen.getByText("Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).")).toBeInTheDocument();
    expect(screen.getByText("Zusatzfoto wird gespeichert…")).toBeInTheDocument();

    await waitFor(async () => {
      const extras = await activeExtras(vehicle.id);
      expect(extras.map((photo) => photo.shotKey)).toEqual(["extra_01", "extra_02"]);
    });
    expect(getAppServices().uploads.getSnapshot().items.filter((item) => item.vehicleId === vehicle.id)).toEqual([]);
  });
});

/** All required photos, capture NOT completed; `rejected`: shots whose saved result asks for a retake. */
async function vehicleReadyToComplete(rejected: string[]) {
  const { backend, template } = getAppServices();
  const data = backend.data;
  const vehicle = await data.createVehicle({
    manufacturer: "Opel",
    model: "Astra",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  for (const shot of template.shots) {
    const { photo } = await savePhotoAndSyncStatus(data, template, {
      photoId: crypto.randomUUID(),
      vehicleId: vehicle.id,
      shotKey: shot.key,
      shotOrder: shot.order,
      title: shot.title,
      file: new Blob(["jpeg"], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: new Date().toISOString(),
    });
    if (rejected.includes(shot.key)) {
      await data.saveProcessedPhoto({
        photo,
        preset: "autoexperten_standard",
        file: new Blob(["result"], { type: "image/jpeg" }),
        warnings: [{ code: "plate_mirrored", message: "Das Foto zeigt die andere Fahrzeugseite." }],
      });
    }
  }
  return vehicle;
}

describe("<PhotoReviewScreen /> – never 'Vollständig' while a shot is red", () => {
  it("shows '15 / 15 · 1 neu aufnehmen' and asks before completing", async () => {
    const vehicle = await vehicleReadyToComplete(["front"]);
    render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    expect(await screen.findByText("1 neu aufnehmen")).toBeInTheDocument();
    expect(screen.getByText("15 / 15 Fotos")).toBeInTheDocument();
    expect(screen.queryByText("Vollständig")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Aufnahmen abschließen" }));
    const question = await screen.findByRole("dialog", {
      name: "1 Foto muss neu aufgenommen werden. Trotzdem abschließen?",
    });
    fireEvent.click(within(question).getByRole("button", { name: "Abbrechen" }));
    expect((await getAppServices().backend.data.getVehicle(vehicle.id))?.status).toBe("capturing");

    fireEvent.click(screen.getByRole("button", { name: "Aufnahmen abschließen" }));
    fireEvent.click(within(question).getByRole("button", { name: "Trotzdem abschließen" }));
    await waitFor(async () =>
      expect((await getAppServices().backend.data.getVehicle(vehicle.id))?.status).toBe("complete"),
    );
  });

  it("uses the plural for several red shots", async () => {
    const vehicle = await vehicleReadyToComplete(["front", "rear"]);
    render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    expect(await screen.findByText("2 neu aufnehmen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Aufnahmen abschließen" }));
    expect(
      await screen.findByRole("dialog", { name: "2 Fotos müssen neu aufgenommen werden. Trotzdem abschließen?" }),
    ).toBeInTheDocument();
  });

  it("completes without a question when no shot is red", async () => {
    const vehicle = await vehicleReadyToComplete([]);
    render(
      <ToastProvider>
        <PhotoReviewScreen vehicleId={vehicle.id} />
      </ToastProvider>,
    );
    expect(await screen.findByText("Vollständig")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Aufnahmen abschließen" }));
    await waitFor(async () =>
      expect((await getAppServices().backend.data.getVehicle(vehicle.id))?.status).toBe("complete"),
    );
    expect(screen.queryByRole("dialog", { name: /Trotzdem abschließen/ })).not.toBeInTheDocument();
  });
});
