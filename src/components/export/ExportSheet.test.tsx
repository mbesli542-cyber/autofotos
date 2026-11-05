/**
 * "Alle Fotos speichern" with the real app services (demo backend in memory):
 * the summary, the share sheet in parts on phones, the ZIP on desktops, and
 * rejected shots only on request.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { installDialogPolyfill } from "@/components/ui/dialog-test-polyfill";
import { getAppServices } from "@/lib/app-services";
import type { Vehicle } from "@/lib/domain/types";
import { buildExportPlan } from "@/lib/export/listing-export";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { getOrderedShots } from "@/lib/shots/shot-template";
import { completeCapture, savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { ExportSheet } from "./ExportSheet";

beforeAll(() => {
  installDialogPolyfill();
});

let referenceCounter = 0;

/** 15 shots (processed unless listed in `unprocessed`) plus "Zusatzfoto 1". */
async function vehicleWithPhotos({ unprocessed = [] as string[], extra = true } = {}) {
  const { backend, template } = getAppServices();
  const data = backend.data;
  referenceCounter += 1;
  const vehicle = await data.createVehicle({
    manufacturer: "Porsche",
    model: "Cayenne",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: `FZ-90${referenceCounter}`,
    notes: null,
  });
  const shots = [
    ...getOrderedShots(template).map((shot) => ({ shotKey: shot.key, shotOrder: shot.order, title: shot.title })),
    ...(extra ? [{ shotKey: "extra_01", shotOrder: 101, title: "Zusatzfoto 1" }] : []),
  ];
  for (const shot of shots) {
    await savePhotoAndSyncStatus(data, template, {
      photoId: crypto.randomUUID(),
      vehicleId: vehicle.id,
      ...shot,
      file: new Blob([`original ${shot.shotKey}`], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: new Date().toISOString(),
    });
  }
  await completeCapture(data, template, vehicle.id);
  for (const photo of await data.listPhotos(vehicle.id)) {
    if (photo.shotKey.startsWith("extra_") || unprocessed.includes(photo.shotKey)) continue;
    await data.saveProcessedPhoto({
      photo,
      preset: "autoexperten_standard",
      file: new Blob([`processed ${photo.shotKey}`], { type: "image/jpeg" }),
      warnings: [],
    });
  }
  return vehicle;
}

async function planFor(vehicle: Vehicle, checks = new Map<string, ShotCheckState>()) {
  const { backend, template } = getAppServices();
  const photos = await backend.data.resolvePhotoUrls(await backend.data.listPhotos(vehicle.id));
  return buildExportPlan({ template, vehicle, slots: buildPhotoSlots(template, photos), checks });
}

function setTouchDevice(touch: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: touch && (query === "(pointer: coarse)" || query === "(hover: none)"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

function setShare(share: ((data: ShareData) => Promise<void>) | undefined, canShare: (data: ShareData) => boolean = () => true) {
  Object.defineProperty(window.navigator, "share", { configurable: true, value: share });
  Object.defineProperty(window.navigator, "canShare", { configurable: true, value: share ? canShare : undefined });
}

/** Captures what downloadBlob hands to the browser. */
function captureDownloads() {
  const blobs = new Map<string, Blob>();
  let n = 0;
  URL.createObjectURL = (blob: Blob | MediaSource) => {
    const url = `blob:download-${++n}`;
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

/** Entry names of a store-only ZIP, read from its central directory. */
async function zipNames(blob: Blob): Promise<string[]> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const end = bytes.length - 22;
  const count = view.getUint16(end + 10, true);
  let position = view.getUint32(end + 16, true);
  const names: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const length = view.getUint16(position + 28, true);
    names.push(new TextDecoder().decode(bytes.subarray(position + 46, position + 46 + length)));
    position += 46 + length;
  }
  return names;
}

const sheet = () => screen.getByRole("dialog", { hidden: true });
const ready = async (name: string | RegExp) => {
  const button = await within(sheet()).findByRole("button", { name, hidden: true });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
};

afterEach(() => {
  vi.restoreAllMocks();
  setShare(undefined);
  setTouchDevice(false);
});

describe("<ExportSheet /> on a phone (share sheet)", () => {
  it("shares the listing set in shot order, in parts of at most 10 files, one tap each", async () => {
    setTouchDevice(true);
    const shared: { name: string; type: string; size: number }[][] = [];
    setShare(async (data) => {
      shared.push((data.files ?? []).map((file) => ({ name: file.name, type: file.type, size: file.size })));
    });
    const vehicle = await vehicleWithPhotos();
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    expect(within(sheet()).getByTestId("export-summary")).toHaveTextContent("15 Fotos bereit · 1 Zusatzfoto");
    expect(within(sheet()).getByText(/Bilder sichern/)).toBeInTheDocument();
    fireEvent.click(await ready("16 Fotos speichern"));
    await waitFor(() => expect(shared).toHaveLength(1));

    fireEvent.click(await ready("Teil 2 von 2 sichern (6 Fotos)"));
    await waitFor(() => expect(shared).toHaveLength(2));

    const ref = vehicle.internalReference;
    expect(shared.map((part) => part.length)).toEqual([10, 6]);
    expect(shared.flat().map((file) => file.name)).toEqual([
      ...getOrderedShots(getAppServices().template).map(
        (shot) => `AE_${ref}_${String(shot.order).padStart(2, "0")}_${shot.key}.jpg`,
      ),
      `AE_${ref}_101_extra_01.jpg`,
    ]);
    expect(shared.flat().every((file) => file.type === "image/jpeg" && file.size > 0)).toBe(true);
    expect(await within(sheet()).findByText("16 Fotos übergeben")).toBeInTheDocument();
  });

  it("stays quiet when the share sheet is closed (AbortError) and the files can be shared again", async () => {
    setTouchDevice(true);
    const share = vi.fn(async () => {
      throw new DOMException("Share canceled", "AbortError");
    });
    setShare(share);
    const vehicle = await vehicleWithPhotos({ extra: false });
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    fireEvent.click(await ready("15 Fotos speichern"));
    await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
    expect(await ready("15 Fotos speichern")).toBeInTheDocument();
    expect(within(sheet()).queryByRole("alert", { hidden: true })).toBeNull();
  });

  it("offers the ZIP when sharing fails (e.g. too many files for the browser)", async () => {
    setTouchDevice(true);
    setShare(async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    });
    const downloads = captureDownloads();
    const vehicle = await vehicleWithPhotos({ extra: false });
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    fireEvent.click(await ready("15 Fotos speichern"));
    expect(await within(sheet()).findByRole("alert", { hidden: true })).toHaveTextContent(
      "Das Teilen hat nicht funktioniert. Bitte die Fotos stattdessen herunterladen.",
    );
    fireEvent.click(await ready("Als ZIP-Datei herunterladen"));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]?.name).toBe(`AE_${vehicle.internalReference}_Fotos.zip`);
  });

  it("falls back to the ZIP on a phone whose browser cannot share files", async () => {
    setTouchDevice(true);
    setShare(async () => {}, () => false);
    const downloads = captureDownloads();
    const vehicle = await vehicleWithPhotos({ extra: false });
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    fireEvent.click(await ready("15 Fotos speichern"));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(await zipNames(downloads[0]!.blob)).toHaveLength(15);
  });
});

describe("<ExportSheet /> on a desktop (ZIP)", () => {
  it("downloads AE_{ref}_Fotos.zip with the files in shot order", async () => {
    const downloads = captureDownloads();
    const vehicle = await vehicleWithPhotos();
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    expect(within(sheet()).getByText(`Als ZIP-Datei „AE_${vehicle.internalReference}_Fotos.zip“.`)).toBeInTheDocument();
    fireEvent.click(await ready("ZIP herunterladen"));
    await waitFor(() => expect(downloads).toHaveLength(1));

    const [download] = downloads;
    expect(download?.name).toBe(`AE_${vehicle.internalReference}_Fotos.zip`);
    expect(download?.blob.type).toBe("application/zip");
    const names = await zipNames(download!.blob);
    expect(names[0]).toBe(`AE_${vehicle.internalReference}_01_front_left_45.jpg`);
    expect(names[14]).toBe(`AE_${vehicle.internalReference}_15_special_detail.jpg`);
    expect(names).toHaveLength(16);
    expect(await within(sheet()).findByText("ZIP-Datei wird heruntergeladen")).toBeInTheDocument();
  });

  it("lists what is not ready and leaves rejected shots out unless they are ticked", async () => {
    const downloads = captureDownloads();
    const vehicle = await vehicleWithPhotos({ unprocessed: ["cockpit"], extra: false });
    const checks = new Map<string, ShotCheckState>([["front", { kind: "retake", reason: "Bitte quer halten." }]]);
    const onShowShot = vi.fn();
    render(
      <ExportSheet open vehicle={vehicle} plan={await planFor(vehicle, checks)} onClose={() => {}} onShowShot={onShowShot} />,
    );

    expect(within(sheet()).getByTestId("export-summary")).toHaveTextContent(
      "13 bearbeitet · 1 noch nicht bearbeitet · 1 muss neu aufgenommen werden",
    );
    const list = within(sheet()).getByRole("list", { name: "Bitte ansehen", hidden: true });
    expect(within(list).getByText("Muss neu aufgenommen werden")).toBeInTheDocument();
    expect(within(list).getByText("Bitte quer halten.")).toBeInTheDocument();
    expect(within(list).getByText("Noch nicht bearbeitet – das Original wird gespeichert")).toBeInTheDocument();
    // A shot in the list opens it in the large view.
    fireEvent.click(within(list).getByRole("button", { name: /Cockpit/, hidden: true }));
    expect(onShowShot).toHaveBeenCalledWith("cockpit");

    // Without the tick: 14 photos, shot 02 left out.
    fireEvent.click(await ready("Trotzdem speichern"));
    await waitFor(() => expect(downloads).toHaveLength(1));
    const names = await zipNames(downloads[0]!.blob);
    expect(names).toHaveLength(14);
    expect(names.some((name) => name.includes("_02_front"))).toBe(false);
    expect(names).toContain(`AE_${vehicle.internalReference}_09_cockpit.jpg`);
  });

  it("saves rejected shots only after the employee ticks them", async () => {
    const downloads = captureDownloads();
    const vehicle = await vehicleWithPhotos({ extra: false });
    const checks = new Map<string, ShotCheckState>([["front", { kind: "retake", reason: "Bitte quer halten." }]]);
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle, checks)} onClose={() => {}} />);

    fireEvent.click(within(sheet()).getByRole("checkbox", { name: /abgelehnte Foto trotzdem mitspeichern/, hidden: true }));
    expect(within(sheet()).getByText(/15 Fotos/)).toBeInTheDocument();
    fireEvent.click(await ready("Trotzdem speichern"));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(await zipNames(downloads[0]!.blob)).toContain(`AE_${vehicle.internalReference}_02_front.jpg`);
  });

  it("says which photo could not be loaded and loads it again on request", async () => {
    const vehicle = await vehicleWithPhotos({ extra: false });
    const data = getAppServices().backend.data;
    const spy = vi.spyOn(data, "getPhotoFile");
    spy.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<ExportSheet open vehicle={vehicle} plan={await planFor(vehicle)} onClose={() => {}} />);

    const alert = await within(sheet()).findByRole("alert", { hidden: true });
    expect(alert).toHaveTextContent("konnte nicht geladen werden");
    expect(alert).toHaveTextContent("Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.");
    await act(async () => {
      fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen", hidden: true }));
    });
    expect(await ready("ZIP herunterladen")).toBeInTheDocument();
  });
});
