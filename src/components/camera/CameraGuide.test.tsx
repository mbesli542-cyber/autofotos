/**
 * Shooting guide in the guided camera: "Tipps für perfekte Fotos" (once per
 * new vehicle, re-openable from the instruction box) and the prompt to turn
 * the phone for exterior shots in portrait.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Vehicle, VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { CameraCapture } from "./CameraCapture";

const stream = vi.hoisted(() => ({ width: 4000, height: 3000 }));

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
vi.mock("@/hooks/use-upload-queue", () => ({ usePendingUploads: () => [] }));
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
const ROTATE = "Bitte Smartphone quer halten";

beforeEach(() => {
  window.localStorage.clear();
  stream.width = 4000;
  stream.height = 3000;
});

describe("'Tipps für perfekte Fotos'", () => {
  it("opens once when the camera opens for a new vehicle and can be re-opened from the instruction box", () => {
    const view = render(camera({ vehicle: vehicle("veh-new") }));
    expect(screen.getByRole("dialog", { name: TIPS_TITLE })).toBeInTheDocument();
    expect(screen.getByText("2–3 m frei hinter dem Auto")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verstanden – los geht’s" }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
    // The shutter is one tap away again.
    expect(screen.getByRole("button", { name: "Foto aufnehmen" })).toBeEnabled();
    view.unmount();

    // The next visit of the same vehicle: no sheet – the light bulb opens it.
    render(camera({ vehicle: vehicle("veh-new") }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: TIPS_TITLE }));
    expect(screen.getByRole("dialog", { name: TIPS_TITLE })).toBeInTheDocument();
    expect(screen.queryByLabelText(/nicht mehr automatisch zeigen/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tipps schließen" }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("does not open by itself for a vehicle that already has photos", () => {
    render(camera({ vehicle: vehicle("veh-started"), photos: [photo("veh-started", 0)] }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("stops opening by itself after 'Bei neuen Fahrzeugen nicht mehr automatisch zeigen'", () => {
    const view = render(camera({ vehicle: vehicle("veh-a") }));
    fireEvent.click(screen.getByLabelText("Bei neuen Fahrzeugen nicht mehr automatisch zeigen"));
    fireEvent.click(screen.getByRole("button", { name: "Verstanden – los geht’s" }));
    view.unmount();
    render(camera({ vehicle: vehicle("veh-b") }));
    expect(screen.queryByRole("dialog", { name: TIPS_TITLE })).not.toBeInTheDocument();
  });

  it("shows the shot's photo hint under the instruction", () => {
    render(camera({ vehicle: vehicle("veh-hint"), photos: [photo("veh-hint", 0)] }));
    // Shot 02 "Vorne" is next.
    expect(screen.getByText("Kennzeichenhalter mit Händlerwerbung und Aufkleber vorher abnehmen.")).toBeInTheDocument();
  });
});

describe("turn the phone for exterior shots", () => {
  it("asks clearly to hold the phone in landscape for an exterior shot in portrait – the shutter still works", () => {
    stream.width = 3000;
    stream.height = 4000;
    render(camera({ vehicle: vehicle("veh-portrait"), photos: [photo("veh-portrait", 0)] }));
    expect(screen.getByText(ROTATE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Foto aufnehmen" })).toBeEnabled();
  });

  it("does not ask in landscape or for interior shots", () => {
    const view = render(camera({ vehicle: vehicle("veh-landscape"), photos: [photo("veh-landscape", 0)] }));
    expect(screen.queryByText(ROTATE)).not.toBeInTheDocument();
    view.unmount();

    stream.width = 3000;
    stream.height = 4000;
    render(camera({ vehicle: vehicle("veh-interior"), photos: [photo("veh-interior", 0)], initialShotKey: "cockpit" }));
    expect(screen.queryByText(ROTATE)).not.toBeInTheDocument();
  });
});
