/**
 * "Fotos bearbeiten" with the real demo services (in-memory IndexedDB
 * fallback, real upload queue and run store). Stubbed: links, the processor
 * status (GET /api/processing-status), the thumbnail decoding of the queue
 * and the processor call itself (`processPhoto` stores a result file like
 * the demo adapter).
 */
import { act, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAppServices } from "@/lib/app-services";
import type { ProcessingPresetId, ProcessingWarning, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import { processPhoto, type PhotoProcessingDeps, type PhotoProcessingResult } from "@/lib/processing/photo-processing";
import { PROCESSOR_STORAGE_MISSING_MESSAGE, type ProcessingStatus } from "@/lib/processing/types";
import { completeCapture, savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { ProcessingScreen } from "./ProcessingScreen";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/camera/image-utils", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/camera/image-utils")>()),
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));

vi.mock("@/lib/processing/photo-processing", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/processing/photo-processing")>()),
  processPhoto: vi.fn(),
}));

// Whole runs over 15 photos against the in-memory demo database: allow for slow CI machines.
configure({ asyncUtilTimeout: 5_000 });

const CONNECTED: ProcessingStatus = {
  connected: true,
  processor: "real",
  showroomSource: "plates",
  showroomError: null,
  connectionError: null,
  accessCodeRequired: false,
  dataBackend: "demo",
};

/** Seed vehicle with placeholder drawings (src/lib/data/mock/seed.ts). */
const SAMPLE_VEHICLE_ID = "5d0c6f1e-63a1-4c84-9a5f-0d5b3f2c6301";

let status: ProcessingStatus = CONNECTED;
let scrolledRows: (string | undefined)[] = [];

/** `processPhoto` stand-in: a real result, saved like the demo adapter does. */
async function saveResult(
  photo: VehiclePhotoWithUrls,
  preset: ProcessingPresetId,
  deps: PhotoProcessingDeps,
  warnings: ProcessingWarning[] = [],
): Promise<PhotoProcessingResult> {
  deps.onStep?.("processing", 0.5);
  await deps.data.saveProcessedPhoto({ photo, preset, file: new Blob(["jpeg"], { type: "image/jpeg" }), warnings });
  return { warnings };
}

function gate() {
  let open: () => void = () => {};
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
}

async function createCompleteVehicle(model: string): Promise<string> {
  const { backend, template } = getAppServices();
  const vehicle = await backend.data.createVehicle({
    manufacturer: "Skoda",
    model,
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  for (const shot of template.shots) {
    await savePhotoAndSyncStatus(backend.data, template, {
      photoId: crypto.randomUUID(),
      vehicleId: vehicle.id,
      shotKey: shot.key,
      shotOrder: shot.order,
      title: shot.title,
      file: new Blob(["original"], { type: "image/jpeg" }),
      thumbnail: null,
      width: 4000,
      height: 3000,
      takenAt: new Date(Date.now() - 60_000).toISOString(),
    });
  }
  await completeCapture(backend.data, template, vehicle.id);
  return vehicle.id;
}

function renderScreen(vehicleId: string) {
  return render(<ProcessingScreen vehicleId={vehicleId} />);
}

const startButton = () => screen.findByRole("button", { name: "Fotos verarbeiten" });

function row(title: string): HTMLElement {
  const list = screen.getByRole("list", { name: "Bearbeitungsfortschritt" });
  const match = within(list)
    .getAllByRole("listitem")
    .find((item) => item.querySelector("p")?.textContent === title);
  if (!match) throw new Error(`no row ${title}`);
  return match;
}

beforeEach(() => {
  status = CONNECTED;
  scrolledRows = [];
  let urls = 0;
  URL.createObjectURL = () => `blob:test-${(urls += 1)}`;
  URL.revokeObjectURL = () => undefined;
  Element.prototype.scrollIntoView = function (this: HTMLElement) {
    scrolledRows.push(this.dataset.shotKey);
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (String(input).endsWith("/api/processing-status")) {
        return new Response(JSON.stringify(status), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new TypeError(`unexpected fetch ${String(input)}`);
    }),
  );
  vi.mocked(processPhoto).mockReset();
  vi.mocked(processPhoto).mockImplementation(saveResult);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

describe("<ProcessingScreen />", () => {
  it("offers only the style the processor implements", async () => {
    renderScreen(await createCompleteVehicle("Styles"));
    await startButton();
    expect(screen.getByRole("radio", { name: /AutoExperten Standard/ })).toHaveAttribute("aria-checked", "true");
    for (const name of [/AutoExperten Dark/, /Original\+/]) {
      const card = screen.getByRole("radio", { name });
      expect(card).toBeDisabled();
      expect(card).toHaveTextContent("Demnächst verfügbar");
    }
  });

  it("waits until new photos of the vehicle are saved before processing", async () => {
    const vehicleId = await createCompleteVehicle("Upload");
    const { backend, uploads } = getAppServices();
    const upload = gate();
    const realSave = backend.data.savePhoto.bind(backend.data);
    const save = vi.spyOn(backend.data, "savePhoto").mockImplementation(async (input) => {
      if (input.vehicleId === vehicleId) await upload.opened;
      return realSave(input);
    });
    try {
      await uploads.enqueue({
        vehicleId,
        shotKey: "front",
        shotOrder: 2,
        title: "Vorne",
        file: new Blob(["retake"], { type: "image/jpeg" }),
        takenAt: new Date().toISOString(),
      });
      renderScreen(vehicleId);
      await waitFor(async () => expect(await startButton()).toBeDisabled());
      expect(screen.getByText("Neue Fotos werden noch gespeichert. Bitte kurz warten.")).toBeInTheDocument();

      await act(async () => upload.open());
      await waitFor(async () => expect(await startButton()).toBeEnabled());
      expect(screen.queryByText("Neue Fotos werden noch gespeichert. Bitte kurz warten.")).not.toBeInTheDocument();
    } finally {
      save.mockRestore();
    }
  });

  it("processes the photo saved a moment ago although the screen still shows the one it replaced", async () => {
    const vehicleId = await createCompleteVehicle("Race");
    const { backend, uploads } = getAppServices();
    const oldFront = (await backend.data.listPhotos(vehicleId)).find((photo) => photo.shotKey === "front")?.id;
    const upload = gate();
    const reload = gate();
    let saved = false;
    const realSave = backend.data.savePhoto.bind(backend.data);
    const save = vi.spyOn(backend.data, "savePhoto").mockImplementation(async (input) => {
      if (input.vehicleId !== vehicleId) return realSave(input);
      await upload.opened;
      const photo = await realSave(input);
      saved = true;
      return photo;
    });
    // Supabase-like: after the save, every page load (signed URLs) takes its time.
    const realResolve = backend.data.resolvePhotoUrls.bind(backend.data);
    const resolve = vi.spyOn(backend.data, "resolvePhotoUrls").mockImplementation(async (photos) => {
      if (saved) await reload.opened;
      return realResolve(photos);
    });
    try {
      renderScreen(vehicleId);
      await waitFor(async () => expect(await startButton()).toBeEnabled());
      const retake = await uploads.enqueue({
        vehicleId,
        shotKey: "front",
        shotOrder: 2,
        title: "Vorne",
        file: new Blob(["retake"], { type: "image/jpeg" }),
        takenAt: new Date().toISOString(),
      });
      await waitFor(async () => expect(await startButton()).toBeDisabled());
      await act(async () => upload.open());
      // Saved – the button is free again while the page's reload is still on its way.
      await waitFor(async () => expect(await startButton()).toBeEnabled());
      fireEvent.click(await startButton());
      await act(async () => reload.open());
      await screen.findByText("Bearbeitung abgeschlossen");

      const fronts = vi.mocked(processPhoto).mock.calls.flatMap(([photo]) => (photo.shotKey === "front" ? [photo.id] : []));
      expect(fronts).toEqual([retake.id]);
      expect(fronts).not.toContain(oldFront);
      expect(screen.getByText("15 von 15 Fotos bearbeitet.")).toBeInTheDocument();
    } finally {
      save.mockRestore();
      resolve.mockRestore();
    }
  });

  it("lists the processor's hints, counts the results to check and offers a retake for a mirrored photo", async () => {
    const vehicleId = await createCompleteVehicle("Hints");
    const mirrored = {
      code: "plate_mirrored",
      message:
        "Das Foto zeigt das Fahrzeug von der anderen Seite – es wurde der Showroom-Hintergrund „Vorne rechts (45°)“ verwendet. Bitte die Aufnahmeposition prüfen.",
    };
    const removed = {
      code: "background_object_removed",
      message:
        "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
    };
    const upscaled = {
      code: "low_resolution",
      message: "Das Fahrzeug musste vergrößert werden – für beste Qualität näher heran oder mit höherer Auflösung fotografieren.",
    };
    const hints: Record<string, ProcessingWarning[]> = { front_left_45: [mirrored], rear: [removed], left_side: [upscaled] };
    vi.mocked(processPhoto).mockImplementation((photo, preset, deps) =>
      saveResult(photo, preset, deps, hints[photo.shotKey] ?? []),
    );
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("Bearbeitung abgeschlossen");

    // 15 saved results – two of them to check (the upscaled one is only a note).
    expect(screen.getByText("15 von 15 Fotos bearbeitet.")).toBeInTheDocument();
    expect(screen.getByText("Bitte 2 Ergebnisse prüfen – siehe Hinweise in der Liste.")).toBeInTheDocument();
    expect(within(row("Vorne links (45°)")).getByText(mirrored.message)).toBeInTheDocument();
    expect(within(row("Vorne links (45°)")).getByRole("link", { name: "Foto neu aufnehmen: Vorne links (45°)" })).toHaveAttribute(
      "href",
      `/fahrzeuge/${vehicleId}/kamera?shot=front_left_45&zurueck=bearbeiten`,
    );
    expect(within(row("Hinten")).getByText(removed.message)).toBeInTheDocument();
    expect(within(row("Hinten")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(row("Linke Fahrzeugseite")).getByText(upscaled.message)).toBeInTheDocument();
    // Kept with the processed version for "Bearbeitet".
    const stored = await getAppServices().backend.data.listPhotos(vehicleId);
    expect(stored.find((photo) => photo.shotKey === "rear")?.processingWarnings).toEqual([removed]);
    expect(stored.find((photo) => photo.shotKey === "front")?.processingWarnings).toEqual([]);
  });

  it("offers the photo retaken during the run for processing and brings its row into view", async () => {
    const vehicleId = await createCompleteVehicle("Retake");
    const { backend, uploads } = getAppServices();
    const oldFront = (await backend.data.listPhotos(vehicleId)).find((photo) => photo.shotKey === "front")?.id;
    const frontJob = gate();
    vi.mocked(processPhoto).mockImplementation(async (photo, preset, deps) => {
      if (photo.id === oldFront) await frontJob.opened;
      return saveResult(photo, preset, deps);
    });
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("Fotos werden verarbeitet…");

    // The new photo of "Front" lands while its old version is being processed.
    const retake = await uploads.enqueue({
      vehicleId,
      shotKey: "front",
      shotOrder: 2,
      title: "Vorne",
      file: new Blob(["retake"], { type: "image/jpeg" }),
      takenAt: new Date().toISOString(),
    });
    await waitFor(async () =>
      expect((await backend.data.listPhotos(vehicleId)).find((p) => p.shotKey === "front")?.id).toBe(retake.id),
    );
    await act(async () => frontJob.open());

    const ready = await screen.findByRole("button", { name: "Neues Foto bereit – jetzt bearbeiten" });
    expect(within(row("Vorne")).getByRole("button", { name: "Neues Foto bearbeiten: Vorne" })).toBeInTheDocument();
    expect(scrolledRows).toContain("front");
    // The summary does not claim every photo is done while the new one is unprocessed.
    expect(screen.getByText("14 von 15 Fotos bearbeitet · 1 neues Foto noch nicht bearbeitet.")).toBeInTheDocument();
    expect((await backend.data.getVehicle(vehicleId))?.status).toBe("complete");

    vi.mocked(processPhoto).mockClear();
    fireEvent.click(ready);
    await waitFor(async () => expect((await backend.data.getVehicle(vehicleId))?.status).toBe("processed"));
    expect(vi.mocked(processPhoto).mock.calls.map(([photo]) => photo.id)).toEqual([retake.id]);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Neues Foto bereit – jetzt bearbeiten" })).not.toBeInTheDocument(),
    );
  });

  it("processes the new photos from the summary at most three at a time", async () => {
    const vehicleId = await createCompleteVehicle("Batch");
    const { template, uploads } = getAppServices();
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("15 von 15 Fotos bearbeitet.");

    // Later the employee retakes five shots.
    for (const shot of template.shots.slice(0, 5)) {
      await uploads.enqueue({
        vehicleId,
        shotKey: shot.key,
        shotOrder: shot.order,
        title: shot.title,
        file: new Blob(["retake"], { type: "image/jpeg" }),
        takenAt: new Date().toISOString(),
      });
    }
    const ready = await screen.findByRole("button", { name: "5 neue Fotos bereit – jetzt bearbeiten" });
    expect(screen.getByText("10 von 15 Fotos bearbeitet · 5 neue Fotos noch nicht bearbeitet.")).toBeInTheDocument();

    const jobs = gate();
    let inFlight = 0;
    let maxInFlight = 0;
    vi.mocked(processPhoto).mockReset();
    vi.mocked(processPhoto).mockImplementation(async (photo, preset, deps) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await jobs.opened;
      const result = await saveResult(photo, preset, deps);
      inFlight -= 1;
      return result;
    });
    fireEvent.click(ready);
    await waitFor(() => expect(inFlight).toBe(3));
    await act(async () => jobs.open());
    await screen.findByText("15 von 15 Fotos bearbeitet.");
    expect(maxInFlight).toBe(3);
    expect(processPhoto).toHaveBeenCalledTimes(5);
  });

  it("keeps a processed shot done when its new capture was refused for good", async () => {
    const vehicleId = await createCompleteVehicle("Refused");
    const { backend, uploads } = getAppServices();
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("15 von 15 Fotos bearbeitet.");

    const realSave = backend.data.savePhoto.bind(backend.data);
    const save = vi.spyOn(backend.data, "savePhoto").mockImplementation(async (input) => {
      if (input.vehicleId === vehicleId) throw new AppError("unsupported_file");
      return realSave(input);
    });
    const refused = await uploads.enqueue({
      vehicleId,
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
      // It can never become the shot's photo ("Fotos überprüfen" offers discarding it).
      expect(within(row("Vorne")).getByText("Fertig")).toBeInTheDocument();
      expect(within(row("Vorne")).queryByRole("button")).not.toBeInTheDocument();
      expect(screen.getByText("15 von 15 Fotos bearbeitet.")).toBeInTheDocument();
    } finally {
      save.mockRestore();
      await uploads.discard(refused.id);
    }
  });

  it("keeps a processed shot done when a capture older than its photo could not be saved", async () => {
    const vehicleId = await createCompleteVehicle("Older capture");
    const { backend, uploads } = getAppServices();
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("15 von 15 Fotos bearbeitet.");

    const realSave = backend.data.savePhoto.bind(backend.data);
    const save = vi.spyOn(backend.data, "savePhoto").mockImplementation(async (input) => {
      if (input.vehicleId === vehicleId) throw new TypeError("Failed to fetch");
      return realSave(input);
    });
    // Taken before the saved photo (e.g. left in the queue of an earlier session): the
    // database stores it archived, the photo slots hide it – it is no new photo.
    const older = await uploads.enqueue({
      vehicleId,
      shotKey: "front",
      shotOrder: 2,
      title: "Vorne",
      file: new Blob(["older"], { type: "image/jpeg" }),
      takenAt: new Date(Date.now() - 20 * 60_000).toISOString(),
    });
    try {
      await waitFor(() =>
        expect(uploads.getSnapshot().items.find((item) => item.id === older.id)).toMatchObject({
          status: "failed",
          retryable: true,
        }),
      );
      expect(within(row("Vorne")).getByText("Fertig")).toBeInTheDocument();
      expect(within(row("Vorne")).queryByText("Neues Foto")).not.toBeInTheDocument();
      expect(within(row("Vorne")).queryByRole("button")).not.toBeInTheDocument();
      expect(screen.getByText("15 von 15 Fotos bearbeitet.")).toBeInTheDocument();
    } finally {
      save.mockRestore();
      await uploads.discard(older.id);
    }
  });

  it("ends the run when the user signs out", async () => {
    const vehicleId = await createCompleteVehicle("SignOut");
    const { authStore, backend } = getAppServices();
    await act(() => authStore.signIn("anna.meier@autoexperten-rn.de", "Werkstatt#2026"));
    const jobs = gate();
    vi.mocked(processPhoto).mockImplementation(async (photo, preset, deps) => {
      await new Promise<void>((resolve, reject) => {
        void jobs.opened.then(resolve);
        deps.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
      return saveResult(photo, preset, deps);
    });
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("Fotos werden verarbeitet…");

    await act(() => authStore.signOut()); // "Abmelden" on a shared device
    // The next person finds the style selection, not the previous user's run …
    expect(screen.getByRole("button", { name: "Fotos verarbeiten" })).toBeInTheDocument();
    expect(screen.queryByText("Fotos werden verarbeitet…")).not.toBeInTheDocument();
    // … and the run does not go on in the background.
    await act(async () => jobs.open());
    expect(processPhoto).toHaveBeenCalledTimes(3);
    expect((await backend.data.listPhotos(vehicleId)).some((photo) => photo.processedStoragePath)).toBe(false);
  });

  it("shows the run still in progress when the screen is re-created and never starts it twice", async () => {
    const vehicleId = await createCompleteVehicle("Evicted");
    const jobs = gate();
    vi.mocked(processPhoto).mockImplementation(async (photo, preset, deps) => {
      await jobs.opened;
      return saveResult(photo, preset, deps);
    });
    const first = renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("Fotos werden verarbeitet…");
    first.unmount(); // route evicted from the router cache

    renderScreen(vehicleId);
    expect(await screen.findByText("Fotos werden verarbeitet…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Fotos verarbeiten" })).not.toBeInTheDocument();

    await act(async () => jobs.open());
    await screen.findByText("Bearbeitung abgeschlossen");
    const processed = vi.mocked(processPhoto).mock.calls.map(([photo]) => photo.id);
    expect(processed).toHaveLength(15);
    expect(new Set(processed).size).toBe(15);
  });

  it("lets a photo that failed for a temporary reason be processed again", async () => {
    const vehicleId = await createCompleteVehicle("Temporary");
    vi.mocked(processPhoto).mockImplementation(async (photo, preset, deps) => {
      if (photo.shotKey === "rear") throw new AppError("network");
      return saveResult(photo, preset, deps);
    });
    renderScreen(vehicleId);
    fireEvent.click(await startButton());
    await screen.findByText("Bearbeitung mit Fehlern abgeschlossen");
    expect(within(row("Hinten")).getByText("Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.")).toBeInTheDocument();

    vi.mocked(processPhoto).mockReset();
    vi.mocked(processPhoto).mockImplementation(saveResult);
    fireEvent.click(within(row("Hinten")).getByRole("button", { name: "Erneut versuchen: Hinten" }));
    await screen.findByText("Bearbeitung abgeschlossen");
    expect(vi.mocked(processPhoto).mock.calls.map(([photo]) => photo.shotKey)).toEqual(["rear"]);
  });

  it("does not offer the demo sample vehicle (placeholder drawings) for processing", async () => {
    renderScreen(SAMPLE_VEHICLE_ID);
    await waitFor(async () => expect(await startButton()).toBeDisabled());
    expect(screen.getByText("Beispielfahrzeug – bitte ein eigenes Fahrzeug fotografieren.")).toBeInTheDocument();
  });

  it("explains a processor without access to the vehicle storage", async () => {
    status = {
      ...CONNECTED,
      connected: false,
      showroomSource: null,
      connectionError: PROCESSOR_STORAGE_MISSING_MESSAGE,
      dataBackend: "supabase",
    };
    renderScreen(await createCompleteVehicle("Storage"));
    expect(await screen.findByText(PROCESSOR_STORAGE_MISSING_MESSAGE)).toBeInTheDocument();
    expect(await startButton()).toBeDisabled();
  });
});
