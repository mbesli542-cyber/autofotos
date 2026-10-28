/**
 * Camera route with the real app services (demo backend in memory, real
 * upload queue). Next.js keeps routes mounted with React <Activity>; the
 * router's bfcacheId changes on every push/replace navigation and stays the
 * same for the browser's Back/Forward.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Activity, type ReactNode } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import { savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { CameraScreen } from "./CameraScreen";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), bfcacheId: "_b_1_" }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("next/link", () => ({
  default: ({ href, children, "aria-label": label }: { href: string; children: ReactNode; "aria-label"?: string }) => (
    <a href={href} aria-label={label}>
      {children}
    </a>
  ),
}));
// Thumbnails need a real image decoder (canvas), which jsdom does not have.
vi.mock("@/lib/camera/image-utils", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/camera/image-utils")>()),
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));
vi.mock("@/hooks/use-camera-stream", () => ({
  useCameraStream: () => ({ state: { status: "ready", width: 4000, height: 3000 }, trackRef: { current: null }, restart: () => {} }),
}));
vi.mock("@/lib/camera/capture", () => ({
  captureStill: async () => new Blob(["jpeg"], { type: "image/jpeg" }),
}));

beforeAll(() => {
  let n = 0;
  URL.createObjectURL = () => `blob:test-${++n}`;
  URL.revokeObjectURL = () => {};
});

const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const heading = () => screen.getByRole("heading", { level: 1 });

/** A vehicle with the first `count` required photos. */
async function vehicleWith(count: number) {
  const { backend, template } = getAppServices();
  const vehicle = await backend.data.createVehicle({
    manufacturer: "Ford",
    model: "Focus",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  for (const shot of template.shots.slice(0, count)) {
    await savePhotoAndSyncStatus(backend.data, template, {
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
  }
  return vehicle;
}

describe("<CameraScreen />", () => {
  it("shows the camera as it was left after Back, and starts a new visit at the requested shot", async () => {
    // Shots 01–08 are there; the employee retakes 03 from the vehicle page (no return target).
    const vehicle = await vehicleWith(8);
    const page = (mode: "visible" | "hidden") => (
      <ToastProvider>
        <Activity mode={mode}>
          <CameraScreen vehicleId={vehicle.id} initialShotKey="front_right_45" returnTo={null} />
        </Activity>
      </ToastProvider>
    );
    const view = render(page("visible"));
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Vorne rechts (45°)");
    fireEvent.click(screen.getByRole("button", { name: "Foto wiederholen" }));
    await wait(50);
    await wait(1100);
    // The guided order continues with the next open shot.
    expect(heading()).toHaveTextContent("Cockpit");

    // A look at the overview (push), then the browser's Back: the same camera, same shot.
    view.rerender(page("hidden"));
    await wait(20);
    view.rerender(page("visible"));
    await wait(50);
    expect(heading()).toHaveTextContent("Cockpit");
    expect(screen.getByRole("button", { name: "Foto aufnehmen" })).toBeEnabled();

    // Later "Foto wiederholen" for 03 again (a new navigation): the camera opens at 03.
    view.rerender(page("hidden"));
    router.bfcacheId = "_b_2_";
    view.rerender(page("visible"));
    await wait(50);
    expect(heading()).toHaveTextContent("Vorne rechts (45°)");
    expect(router.push).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  });
});
