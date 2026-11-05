/** "Speichern" in the large view, with the real app services (demo backend in memory). */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getAppServices } from "@/lib/app-services";
import type { VehiclePhoto } from "@/lib/domain/types";
import { SavePhotoButton } from "./SavePhotoButton";

async function photoWithResult() {
  const { backend } = getAppServices();
  const vehicle = await backend.data.createVehicle({
    manufacturer: "Audi",
    model: "Q5",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: `FZ-S${Math.random().toString(36).slice(2, 8)}`,
    notes: null,
  });
  const original = await backend.data.savePhoto({
    photoId: crypto.randomUUID(),
    vehicleId: vehicle.id,
    shotKey: "front_left_45",
    shotOrder: 1,
    title: "Vorne links (45°)",
    file: new Blob(["original"], { type: "image/png" }),
    thumbnail: null,
    width: 4000,
    height: 3000,
    takenAt: "2026-11-05T09:00:00.000Z",
  });
  const photo: VehiclePhoto = await backend.data.saveProcessedPhoto({
    photo: original,
    preset: "autoexperten_standard",
    file: new Blob(["processed"], { type: "image/jpeg" }),
    warnings: [],
  });
  return { vehicle, photo };
}

function setTouchDevice(touch: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: touch && (query === "(pointer: coarse)" || query === "(hover: none)"),
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

function setNavigator(values: Record<string, unknown>) {
  for (const [key, value] of Object.entries(values)) {
    Object.defineProperty(window.navigator, key, { configurable: true, value });
  }
}

function captureDownloads() {
  const blobs = new Map<string, Blob>();
  URL.createObjectURL = (blob: Blob | MediaSource) => {
    const url = `blob:photo-${blobs.size + 1}`;
    blobs.set(url, blob as Blob);
    return url;
  };
  URL.revokeObjectURL = () => {};
  const downloads: { name: string; blob: Blob }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    const blob = blobs.get(this.href);
    if (blob) downloads.push({ name: this.download, blob });
  });
  return downloads;
}

afterEach(() => {
  vi.restoreAllMocks();
  setTouchDevice(false);
  setNavigator({ share: undefined, canShare: undefined, userActivation: undefined });
});

describe("<SavePhotoButton />", () => {
  it("downloads the version shown under its export name on a desktop", async () => {
    const downloads = captureDownloads();
    const { vehicle, photo } = await photoWithResult();
    const view = render(<SavePhotoButton vehicle={vehicle} photo={photo} version="processed" />);

    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe(`AE_${vehicle.internalReference}_01_front_left_45.jpg`);
    expect(await downloads[0]?.blob.text()).toBe("processed");
    expect(await screen.findByRole("status")).toHaveTextContent(`Gespeichert als AE_${vehicle.internalReference}_01_front_left_45.jpg`);

    // The original keeps its own type.
    view.rerender(<SavePhotoButton vehicle={vehicle} photo={photo} version="original" />);
    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    await waitFor(() => expect(downloads).toHaveLength(2));
    expect(downloads[1]?.name).toBe(`AE_${vehicle.internalReference}_01_front_left_45.png`);
    expect(await downloads[1]?.blob.text()).toBe("original");
  });

  it("opens the share sheet on a phone – after a second tap when loading took too long", async () => {
    setTouchDevice(true);
    const shared: File[][] = [];
    setNavigator({
      share: vi.fn(async (data: ShareData) => {
        shared.push(data.files ?? []);
      }),
      canShare: () => true,
      userActivation: { isActive: false },
    });
    const { vehicle, photo } = await photoWithResult();
    render(<SavePhotoButton vehicle={vehicle} photo={photo} version="processed" />);

    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    const now = await screen.findByRole("button", { name: "Foto jetzt speichern" });
    expect(shared).toHaveLength(0);
    fireEvent.click(now);
    await waitFor(() => expect(shared).toHaveLength(1));
    expect(shared[0]?.map((file) => [file.name, file.type])).toEqual([
      [`AE_${vehicle.internalReference}_01_front_left_45.jpg`, "image/jpeg"],
    ]);
  });

  it("shares at once while the tap is fresh, and downloads when sharing fails", async () => {
    setTouchDevice(true);
    const downloads = captureDownloads();
    const share = vi.fn(async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    });
    setNavigator({ share, canShare: () => true, userActivation: { isActive: true } });
    const { vehicle, photo } = await photoWithResult();
    render(<SavePhotoButton vehicle={vehicle} photo={photo} version="processed" />);

    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent("Teilen nicht möglich – das Foto wird heruntergeladen.");
    expect(downloads.map((download) => download.name)).toEqual([`AE_${vehicle.internalReference}_01_front_left_45.jpg`]);
  });

  it("says neutrally that the share sheet took the photo", async () => {
    setTouchDevice(true);
    setNavigator({ share: vi.fn(async () => {}), canShare: () => true, userActivation: { isActive: true } });
    const { vehicle, photo } = await photoWithResult();
    render(<SavePhotoButton vehicle={vehicle} photo={photo} version="processed" />);
    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      `AE_${vehicle.internalReference}_01_front_left_45.jpg an das Teilen-Menü übergeben.`,
    );
  });

  it("downloads on a phone whose browser cannot share the file – never promising the share sheet", async () => {
    setTouchDevice(true);
    const share = vi.fn(async () => {});
    setNavigator({ share, canShare: () => false, userActivation: { isActive: true } });
    const downloads = captureDownloads();
    const { vehicle, photo } = await photoWithResult();
    render(<SavePhotoButton vehicle={vehicle} photo={photo} version="original" />);
    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(share).not.toHaveBeenCalled();
    expect(downloads[0]?.name).toBe(`AE_${vehicle.internalReference}_01_front_left_45.png`);
  });

  it("names an extra photo as in the whole set (after the template shots)", async () => {
    const downloads = captureDownloads();
    const { vehicle, photo } = await photoWithResult();
    const extra = await getAppServices().backend.data.savePhoto({
      photoId: crypto.randomUUID(),
      vehicleId: vehicle.id,
      shotKey: "extra_02",
      shotOrder: 102,
      title: "Zusatzfoto 2",
      file: new Blob(["extra"], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: photo.takenAt,
    });
    render(<SavePhotoButton vehicle={vehicle} photo={extra} version="original" />);
    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe(`AE_${vehicle.internalReference}_17_extra_02.jpg`);
  });

  it("says in German why a photo could not be loaded", async () => {
    const { vehicle, photo } = await photoWithResult();
    render(<SavePhotoButton vehicle={vehicle} photo={{ ...photo, processedStoragePath: null }} version="processed" />);
    fireEvent.click(screen.getByRole("button", { name: "Foto speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Diese Version des Fotos ist nicht mehr vorhanden.");
  });
});
