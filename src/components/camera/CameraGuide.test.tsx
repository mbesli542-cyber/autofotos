/**
 * Shooting guide in the guided camera: "Tipps für perfekte Fotos" (by itself
 * once per device for a new vehicle, re-openable from the instruction box),
 * the prompt to turn the phone for exterior shots in portrait – and what the
 * camera says when the device's storage is full.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Vehicle, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { USER_ERROR_MESSAGES } from "@/lib/errors";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { UNSAVED_PHOTOS_LOST_HINT } from "@/lib/offline/upload-status";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { CameraCapture } from "./CameraCapture";
import { ROTATE_PROMPT_MS } from "@/lib/shots/shooting-guide";

const stream = vi.hoisted(() => ({ width: 4000, height: 3000 }));
const queue = vi.hoisted(() => ({ items: [] as unknown[] }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("@/lib/app-services", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/app-services")>()),
  getAppServices: () => ({ uploads: { enqueue: vi.fn(), retryAll: vi.fn() } }),
}));
vi.mock("@/hooks/use-upload-queue", () => ({ usePendingUploads: () => queue.items }));
vi.mock("@/hooks/use-photo-checks", async () => {
  const { getShotCheckStates } = await import("@/lib/processing/shot-check");
  const snapshot = { availability: "unknown", checks: new Map(), captures: new Map() } as const;
  return {
    useShotCheckStates: (vehicleId: string, slots: Parameters<typeof getShotCheckStates>[0]) => ({
      states: getShotCheckStates(slots, vehicleId, snapshot),
      snapshot,
    }),
  };
});
vi.mock("@/hooks/use-camera-stream", () => ({
  useCameraStream: () => ({
    state: { status: "ready", width: stream.width, height: stream.height },
    trackRef: { current: null },
    restart: () => {},
  }),
}));

function vehicle(id: string): Vehicle {
  return {
    id,
    userId: "demo-user",
    manufacturer: "BMW",
    model: "320d",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
    status: "capturing",
    createdAt: "2026-11-05T10:00:00.000Z",
    updatedAt: "2026-11-05T10:00:00.000Z",
  };
}

function photo(vehicleId: string, index: number): VehiclePhotoWithUrls {
  const shot = T.shots[index];
  if (!shot) throw new Error("no shot");
  return {
    id: `p_${vehicleId}_${shot.key}`,
    vehicleId,
    shotKey: shot.key,
    shotOrder: shot.order,
    title: shot.title,
    originalStoragePath: `${vehicleId}/${shot.key}/p.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt: "2026-11-05T10:00:00.000Z",
    createdAt: "2026-11-05T10:00:00.000Z",
    updatedAt: "2026-11-05T10:00:00.000Z",
    urls: { original: "/o.jpg", thumbnail: "/t.jpg", processed: null },
  };
}

function camera(props: Partial<ComponentProps<typeof CameraCapture>> & { vehicle: Vehicle }) {
  return (
    <ToastProvider>
      <CameraCapture photos={[]} template={T} initialShotKey={null} returnTo={null} {...props} />
    </ToastProvider>
  );
}

const TIPS_TITLE = "Tipps für perfekte Fotos";
const ROTATE = /^Bitte Smartphone quer halten/;
const ROTATE_LINE = "Smartphone quer halten";
/** The header's status line asking to turn the phone (null: not shown). */
const rotatePrompt = () => screen.queryByRole("button", { name: ROTATE });

beforeEach(() => {
  window.localStorage.clear();
  stream.width = 4000;
  stream.height = 3000;
  queue.items = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe("'Tipps für perfekte Fotos'", () => {
  it("opens by itself once per device for a new vehicle and can be re-opened from the instruction box", () => {
    const view = render(camera({ vehicle: vehicle("veh-new") }));
    expect(screen.getByRole("dialog", { name: TIPS_TITLE })).toBeInTheDocument();
    expect(screen.getByText("2–3 m frei hinter dem Auto")).toBeInTheDocument();
    // The primary action has the focus: Enter closes the tips.
    expect(screen.getByRole("button", { name: "Verstanden – los geht’s" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Verstanden – los geht’s" }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
    // The shutter is one tap away again.
    expect(screen.getByRole("button", { name: "Foto aufnehmen" })).toBeEnabled();
    view.unmount();

    // The next new vehicle on this device: no extra tap – the light bulb opens the tips.
    render(camera({ vehicle: vehicle("veh-next") }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: TIPS_TITLE }));
    expect(screen.getByRole("dialog", { name: TIPS_TITLE })).toBeInTheDocument();
    expect(screen.queryByLabelText(/nicht mehr automatisch zeigen/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tipps schließen" }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("closes with Esc and with a tap outside the sheet", () => {
    render(camera({ vehicle: vehicle("veh-esc"), photos: [photo("veh-esc", 0)] }));
    fireEvent.click(screen.getByRole("button", { name: TIPS_TITLE }));
    // Esc on a modal <dialog> fires "cancel".
    fireEvent(screen.getByRole("dialog", { name: TIPS_TITLE }), new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: TIPS_TITLE }));
    fireEvent.click(screen.getByRole("dialog", { name: TIPS_TITLE })); // the backdrop
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("does not open by itself for a vehicle that already has photos", () => {
    render(camera({ vehicle: vehicle("veh-started"), photos: [photo("veh-started", 0)] }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("shows the shot's photo hint under the instruction", () => {
    render(camera({ vehicle: vehicle("veh-hint"), photos: [photo("veh-hint", 0)] }));
    // Shot 02 "Vorne" is next.
    expect(screen.getByText("Kennzeichenhalter mit Händlerwerbung und Aufkleber vorher abnehmen.")).toBeInTheDocument();
  });
});

describe("turn the phone for exterior shots", () => {
  it("asks clearly to hold the phone in landscape – briefly in the header, then in the instruction box; the shutter works", () => {
    vi.useFakeTimers();
    stream.width = 3000;
    stream.height = 4000;
    render(camera({ vehicle: vehicle("veh-portrait"), photos: [photo("veh-portrait", 0)] }));
    // In the header's status line – never on the framing guide.
    expect(rotatePrompt()?.closest("header")).not.toBeNull();
    expect(screen.getByText(ROTATE_LINE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Foto aufnehmen" })).toBeEnabled();
    act(() => {
      vi.advanceTimersByTime(ROTATE_PROMPT_MS);
    });
    expect(rotatePrompt()).toBeNull();
    expect(screen.getByText(ROTATE_LINE)).toBeInTheDocument();
    // Not before every shot: the next exterior shot keeps the compact line only.
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(rotatePrompt()).toBeNull();
    expect(screen.getByText(ROTATE_LINE)).toBeInTheDocument();
  });

  it("opens its full text on a tap, names the setting on iPhone and Android, and goes once closed", () => {
    stream.width = 3000;
    stream.height = 4000;
    render(camera({ vehicle: vehicle("veh-tap"), photos: [photo("veh-tap", 0)] }));
    fireEvent.click(rotatePrompt() as HTMLElement);
    const details = document.getElementById("camera-check-details");
    expect(details).toHaveTextContent(/Ausrichtungssperre aus · Android: „Automatisch drehen“ an/);
    fireEvent.click(screen.getByRole("button", { name: "Hinweis schließen" }));
    expect(rotatePrompt()).toBeNull();
    expect(screen.getByText(ROTATE_LINE)).toBeInTheDocument();
  });

  it("does not ask in landscape or for interior shots", () => {
    const view = render(camera({ vehicle: vehicle("veh-landscape"), photos: [photo("veh-landscape", 0)] }));
    expect(rotatePrompt()).toBeNull();
    expect(screen.queryByText(ROTATE_LINE)).not.toBeInTheDocument();
    view.unmount();

    stream.width = 3000;
    stream.height = 4000;
    render(camera({ vehicle: vehicle("veh-interior"), photos: [photo("veh-interior", 0)], initialShotKey: "cockpit" }));
    expect(rotatePrompt()).toBeNull();
    expect(screen.queryByText(ROTATE_LINE)).not.toBeInTheDocument();
  });
});

describe("full device storage", () => {
  function refused(id: string, shotKey: string): UploadItem {
    const shot = T.shots.find((entry) => entry.key === shotKey);
    return {
      id,
      vehicleId: "veh-full",
      shotKey,
      shotOrder: shot?.order ?? 0,
      title: shot?.title ?? shotKey,
      takenAt: "2026-11-05T10:05:00.000Z",
      status: "failed",
      thumbnailUrl: null,
      error: USER_ERROR_MESSAGES.storage_full,
      retryable: true,
      unpersisted: true,
      storageFull: true,
    };
  }

  it("says the storage is full (not just 'Erneut versuchen') and that unsaved photos are lost when the app closes", async () => {
    const props = { vehicle: vehicle("veh-full"), photos: [photo("veh-full", 0)] };
    const view = render(camera(props));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    queue.items = [refused("u_front", "front")];
    view.rerender(camera(props));
    expect(
      screen.getByRole("button", { name: "1 nicht gespeichert · Speicher voll – bitte Fahrzeuge löschen, dann erneut versuchen" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Erneut versuchen$/ })).not.toBeInTheDocument();
    // In the header's status line – never a message on the framing guide.
    const line = await screen.findByRole("alert");
    expect(line).toHaveTextContent(
      `Speicher voll – Foto nicht gespeichert: ${USER_ERROR_MESSAGES.storage_full} ${UNSAVED_PHOTOS_LOST_HINT}`,
    );
    expect(line.closest("header")).not.toBeNull();
  });
});
