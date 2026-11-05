/**
 * Guided camera: unknown shot links, hidden routes (Next.js keeps routes
 * mounted with React <Activity>), picked files, leaving the camera and the
 * header in short (landscape) viewports.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Activity, type ComponentProps, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Vehicle, VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { NewPendingUpload } from "@/lib/offline/upload-queue";
import { photoChecks, type PhotoCheck, type PhotoChecksSnapshot } from "@/lib/processing/photo-checks";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { CameraCapture } from "./CameraCapture";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }));
const uploads = vi.hoisted(() => ({ enqueue: vi.fn(), retryAll: vi.fn() }));
const queue = vi.hoisted(() => ({ items: [] as unknown[] }));
/** The automatic checks as the camera sees them (photo-checks.ts). */
const checks = vi.hoisted(() => ({
  snapshot: { availability: "unknown", checks: new Map(), captures: new Map() } as {
    availability: string;
    checks: Map<string, unknown>;
    captures: Map<string, unknown>;
  },
}));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
// Like next/link: a click navigates (push or replace) unless onNavigate prevents it.
vi.mock("next/link", () => ({
  default: ({
    href,
    replace,
    onNavigate,
    children,
    ...props
  }: {
    href: string;
    replace?: boolean;
    onNavigate?: (event: { preventDefault: () => void }) => void;
    children: ReactNode;
  }) => (
    <a
      href={href}
      data-replace={replace ? "true" : undefined}
      onClick={(event) => {
        event.preventDefault();
        let prevented = false;
        onNavigate?.({ preventDefault: () => (prevented = true) });
        if (!prevented) (replace ? router.replace : router.push)(href);
      }}
      {...props}
    >
      {children}
    </a>
  ),
}));
vi.mock("@/lib/app-services", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/app-services")>()),
  getAppServices: () => ({ uploads }),
}));
vi.mock("@/hooks/use-upload-queue", () => ({ usePendingUploads: () => queue.items }));
vi.mock("@/hooks/use-photo-checks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-photo-checks")>();
  const { getShotCheckStates } = await import("@/lib/processing/shot-check");
  return {
    ...actual,
    useShotCheckStates: (vehicleId: string, slots: Parameters<typeof getShotCheckStates>[0]) => {
      const snapshot = checks.snapshot as PhotoChecksSnapshot;
      actual.useVehicleChecks(vehicleId, snapshot.availability);
      return { states: getShotCheckStates(slots, vehicleId, snapshot), snapshot };
    },
  };
});
vi.mock("@/hooks/use-camera-stream", () => ({
  useCameraStream: () => ({
    state: { status: "ready", width: 4000, height: 3000 },
    trackRef: { current: null },
    restart: () => {},
  }),
}));
vi.mock("@/lib/camera/capture", () => ({
  captureStill: async () => new Blob(["jpeg"], { type: "image/jpeg" }),
}));

const VEHICLE: Vehicle = {
  id: "veh_1",
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
  createdAt: "2026-10-28T10:00:00.000Z",
  updatedAt: "2026-10-28T10:00:00.000Z",
};

function photo(shotKey: string, shotOrder: number, title: string, takenAt = "2026-10-28T10:00:00.000Z"): VehiclePhotoWithUrls {
  return {
    id: `p_${shotKey}`,
    vehicleId: VEHICLE.id,
    shotKey,
    shotOrder,
    title,
    originalStoragePath: `${VEHICLE.id}/${shotKey}/p_${shotKey}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt,
    createdAt: "2026-10-28T10:00:00.000Z",
    updatedAt: "2026-10-28T10:00:00.000Z",
    urls: { original: `/o/${shotKey}.jpg`, thumbnail: `/t/${shotKey}.jpg`, processed: null },
  };
}

/** Saved photos for the first `count` shots of the template. */
const photosOf = (count: number) => T.shots.slice(0, count).map((s) => photo(s.key, s.order, s.title));

type CameraProps = ComponentProps<typeof CameraCapture>;

function camera(props: Partial<CameraProps> = {}, mode: "visible" | "hidden" = "visible") {
  return (
    <ToastProvider>
      <Activity mode={mode}>
        <CameraCapture vehicle={VEHICLE} photos={[]} template={T} initialShotKey={null} returnTo={null} {...props} />
      </Activity>
    </ToastProvider>
  );
}

const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));

function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("no file input");
  return input;
}

async function pick(container: HTMLElement, file: File) {
  await act(async () => {
    fireEvent.change(fileInput(container), { target: { files: [file] } });
  });
}

const enqueued = () => uploads.enqueue.mock.calls.map(([input]) => input as NewPendingUpload);
const shutter = (name: "Foto aufnehmen" | "Foto wiederholen") => screen.getByRole("button", { name });
const heading = () => screen.getByRole("heading", { level: 1 });
const UNKNOWN_SHOT_HINT = "Diese Aufnahme gibt es in der Kamera nicht. Es geht mit den Pflichtfotos weiter.";
const CHOOSE_SHOT_HINT = "Tippen Sie oben auf das Foto, das Sie wiederholen möchten.";

/** The browser's session history (Navigation API) with the camera opened from `previousPath`. */
function openedFrom(previousPath: string) {
  const entries = [{ url: new URL(previousPath, window.location.href).href }, { url: window.location.href }];
  Object.defineProperty(window, "navigation", {
    configurable: true,
    value: { currentEntry: { index: 1 }, entries: () => entries },
  });
}

let objectUrls = 0;

beforeEach(() => {
  Reflect.deleteProperty(window, "navigation");
  window.history.replaceState(null, "");
  router.push.mockReset();
  router.replace.mockReset();
  router.back.mockReset();
  uploads.enqueue.mockReset();
  uploads.enqueue.mockImplementation(async (input: NewPendingUpload) => ({ id: "u_new", ...input, status: "queued" }));
  uploads.retryAll.mockReset();
  queue.items = [];
  checks.snapshot = { availability: "unknown", checks: new Map(), captures: new Map() };
  objectUrls = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${++objectUrls}`);
  URL.revokeObjectURL = vi.fn();
});

describe("<CameraCapture /> requested shot", () => {
  it("never replaces a photo by itself for an unknown shot (e.g. an additional photo)", async () => {
    const view = render(camera({ photos: photosOf(15), initialShotKey: "extra_01" }));
    // Every required photo is there: nothing is captured until the employee chooses one.
    expect(heading()).toHaveTextContent("Vorne links (45°)");
    expect(screen.getByText(`Diese Aufnahme gibt es in der Kamera nicht. ${CHOOSE_SHOT_HINT}`)).toBeInTheDocument();
    expect(shutter("Foto wiederholen")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Foto hochladen" })).toBeDisabled();
    fireEvent.click(shutter("Foto wiederholen"));
    await pick(view.container, new File(["jpeg"], "IMG_0001.jpg", { type: "image/jpeg" }));
    await wait(20);
    expect(uploads.enqueue).not.toHaveBeenCalled();

    // The employee picks the photo to replace: the hint is gone, the camera captures it.
    fireEvent.click(screen.getByRole("button", { name: "2. Vorne – aufgenommen" }));
    expect(heading()).toHaveTextContent("Vorne");
    expect(screen.queryByText(/Diese Aufnahme gibt es in der Kamera nicht/)).toBeNull();
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    expect(enqueued().map((input) => input.shotKey)).toEqual(["front"]);
  });

  it("asks which photo to replace when every photo is there and no shot was requested", () => {
    render(camera({ photos: photosOf(15) }));
    expect(screen.getByText(`Alle Pflichtfotos sind aufgenommen. ${CHOOSE_SHOT_HINT}`)).toBeInTheDocument();
    expect(shutter("Foto wiederholen")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(heading()).toHaveTextContent("Vorne");
    expect(screen.queryByText(/Alle Pflichtfotos sind aufgenommen/)).toBeNull();
    expect(shutter("Foto wiederholen")).toBeEnabled();
  });

  it("continues with the first open shot for an unknown shot and stores the photo there", async () => {
    render(camera({ photos: photosOf(3), initialShotKey: "extra_01" }));
    expect(heading()).toHaveTextContent("Linke Fahrzeugseite");
    expect(screen.getByText(UNKNOWN_SHOT_HINT)).toBeInTheDocument();

    fireEvent.click(shutter("Foto aufnehmen"));
    await wait(20);
    expect(enqueued().map((input) => input.shotKey)).toEqual(["left_side"]);
  });

  it("opens a requested template shot without a hint", () => {
    render(camera({ photos: photosOf(15), initialShotKey: "rear", returnTo: "fotos" }));
    expect(heading()).toHaveTextContent("Hinten");
    expect(screen.queryByText(UNKNOWN_SHOT_HINT)).toBeNull();
    expect(screen.queryByText(new RegExp(CHOOSE_SHOT_HINT))).toBeNull();
    expect(shutter("Foto wiederholen")).toBeEnabled();
  });

  it("stores a retake with its real capture time, even if the photo it replaces is dated ahead", async () => {
    // Which photo is the current one follows the database's rule (capture-order.ts) – the
    // retake is never dated after a photo from a camera whose clock runs ahead.
    const ahead = "2027-01-01T00:00:00.000Z";
    const photos = [...photosOf(6), photo("rear", 7, "Hinten", ahead), ...photosOf(15).slice(7)];
    render(camera({ photos, initialShotKey: "rear", returnTo: "fotos" }));
    const before = Date.now();
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    const [retake] = enqueued();
    expect(retake?.shotKey).toBe("rear");
    const takenAt = Date.parse(retake?.takenAt ?? "");
    expect(takenAt).toBeGreaterThanOrEqual(before);
    expect(takenAt).toBeLessThanOrEqual(Date.now());
  });
});

describe("<CameraCapture /> after a reload", () => {
  const heading = () => screen.getByRole("heading", { level: 1 });

  it("opens at the shot it showed, not at the shot of the link it was opened with", () => {
    const first = render(camera({ photos: photosOf(15), initialShotKey: "front" }));
    expect(heading()).toHaveTextContent("Vorne");
    // The camera moves on (next rejected shot, a tapped segment): kept with its history entry.
    fireEvent.click(screen.getByRole("button", { name: /^7\. Hinten –/ }));
    expect(heading()).toHaveTextContent("Hinten");
    expect(window.history.state).toMatchObject({ aeCameraShot: { vehicleId: VEHICLE.id, shotKey: "rear" } });
    first.unmount();

    // Reload / discarded tab: same entry, same URL (?shot=front).
    render(camera({ photos: photosOf(15), initialShotKey: "front" }));
    expect(heading()).toHaveTextContent("Hinten");
  });

  it("ignores what another vehicle's camera or an unknown shot left in the entry", () => {
    window.history.replaceState({ aeCameraShot: { vehicleId: "veh_other", shotKey: "rear" } }, "");
    const view = render(camera({ photos: photosOf(3), initialShotKey: "front" }));
    expect(heading()).toHaveTextContent("Vorne");
    view.unmount();
    window.history.replaceState({ aeCameraShot: { vehicleId: VEHICLE.id, shotKey: "extra_01" } }, "");
    render(camera({ photos: photosOf(3) }));
    // The guided order applies: the first open shot.
    expect(heading()).toHaveTextContent("Linke Fahrzeugseite");
  });
});

describe("<CameraCapture /> when its route gets hidden", () => {
  it("drops a capture preview, so opening the camera again neither replays it nor leaves", async () => {
    const props = { initialShotKey: "front", returnTo: "fotos" } as const;
    const view = render(camera(props));
    fireEvent.click(shutter("Foto aufnehmen"));
    await wait(20);
    expect(screen.getByText("Foto aufgenommen")).toBeInTheDocument();

    // "Kamera schließen" within the preview time …
    view.rerender(camera(props, "hidden"));
    await wait(1100);
    // … and the browser's Back shows the same (preserved) camera again.
    view.rerender(camera(props, "visible"));
    await wait(1100);

    expect(screen.queryByText("Foto aufgenommen")).toBeNull();
    expect(view.container.querySelector('img[alt^="Aufgenommenes Foto"]')).toBeNull();
    expect(shutter("Foto aufnehmen")).toBeEnabled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
    expect(router.back).not.toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(enqueued()).toHaveLength(1);
  });

  it("shows no preview for a capture that finished while the camera was hidden", async () => {
    let finishSaving: () => void = () => {};
    uploads.enqueue.mockImplementationOnce(
      (input: NewPendingUpload) =>
        new Promise((resolve) => {
          finishSaving = () => resolve({ id: "u_slow", ...input, status: "queued" });
        }),
    );
    const props = { initialShotKey: "front", returnTo: "fotos" } as const;
    const view = render(camera(props));
    fireEvent.click(shutter("Foto aufnehmen"));
    await wait(10);

    view.rerender(camera(props, "hidden"));
    await act(async () => finishSaving());
    await wait(20);
    view.rerender(camera(props, "visible"));
    await wait(1100);

    expect(screen.queryByText("Foto aufgenommen")).toBeNull();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("continues with the next shot when a capture finishes while the camera is hidden", async () => {
    let finishSaving: () => void = () => {};
    uploads.enqueue.mockImplementationOnce(
      (input: NewPendingUpload) =>
        new Promise((resolve) => {
          finishSaving = () => resolve({ id: "u_slow", ...input, status: "queued" });
        }),
    );
    const props = { photos: photosOf(8), initialShotKey: "front_right_45" };
    const view = render(camera(props));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(10);

    view.rerender(camera(props, "hidden"));
    await act(async () => finishSaving());
    await wait(20);
    view.rerender(camera(props, "visible"));
    expect(heading()).toHaveTextContent("Cockpit");
    expect(screen.queryByText("Foto aufgenommen")).toBeNull();
  });

  it("shows the camera as it was left after the browser's Back", async () => {
    // Retake of 03 from the vehicle page (?shot=, no return target): the guided order continues.
    const props = { photos: photosOf(8), initialShotKey: "front_right_45" };
    const view = render(camera(props));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    await wait(1100);
    expect(heading()).toHaveTextContent("Cockpit");

    // A look at the overview (grid link), then Back: still at the next shot – never back at 03.
    view.rerender(camera(props, "hidden"));
    await wait(10);
    view.rerender(camera(props, "visible"));
    expect(heading()).toHaveTextContent("Cockpit");
    expect(shutter("Foto aufnehmen")).toBeEnabled();
    expect(screen.queryByText("Bereits aufgenommen")).toBeNull();
  });

  it("continues with the next shot when the camera is left during a preview", async () => {
    const props = { photos: photosOf(8), initialShotKey: "front_right_45" };
    const view = render(camera(props));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    expect(screen.getByText("Foto aufgenommen")).toBeInTheDocument();

    view.rerender(camera(props, "hidden"));
    await wait(1100);
    view.rerender(camera(props, "visible"));
    await wait(1100);
    expect(heading()).toHaveTextContent("Cockpit");
    expect(screen.queryByText("Foto aufgenommen")).toBeNull();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe("<CameraCapture /> toasts", () => {
  it("moves toasts off the header while the camera is shown, and back when it is hidden", async () => {
    const props = { initialShotKey: "front", returnTo: "fotos" } as const;
    const view = render(camera(props));
    expect(document.documentElement).toHaveAttribute("data-camera-open");
    view.rerender(camera(props, "hidden"));
    await wait(10);
    expect(document.documentElement).not.toHaveAttribute("data-camera-open");
    view.rerender(camera(props, "visible"));
    await wait(10);
    expect(document.documentElement).toHaveAttribute("data-camera-open");
    view.unmount();
    expect(document.documentElement).not.toHaveAttribute("data-camera-open");
  });
});

describe("<CameraCapture /> picked files", () => {
  it("offers and accepts only the formats and sizes the storage takes", async () => {
    const view = render(camera());
    // Without HEIC: iOS then converts library photos to JPEG (see photo-file.ts).
    expect(fileInput(view.container)).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");

    await pick(view.container, new File(["gif"], "animation.gif", { type: "image/gif" }));
    expect(screen.getByText("Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).")).toBeInTheDocument();

    const huge = new File(["jpeg"], "gross.jpg", { type: "image/jpeg" });
    Object.defineProperty(huge, "size", { value: 60 * 1024 * 1024 });
    await pick(view.container, huge);
    expect(screen.getByText("Das Foto ist zu groß (max. 50 MB).")).toBeInTheDocument();
    expect(uploads.enqueue).not.toHaveBeenCalled();

    // HEIC without a type (e.g. on Windows): accepted with the type taken from the extension.
    await pick(view.container, new File(["heic"], "IMG_0001.HEIC", { type: "" }));
    expect(enqueued().map((input) => [input.shotKey, input.file.type])).toEqual([["front_left_45", "image/heic"]]);
  });
});

describe("<CameraCapture /> leaving the camera", () => {
  it("returns from a retake by replacing the camera in the history", async () => {
    render(camera({ photos: photosOf(15), initialShotKey: "front_right_45", returnTo: "fotos" }));
    expect(screen.getByRole("link", { name: "Kamera schließen" })).toHaveAttribute("data-replace", "true");

    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    expect(screen.getByText("Foto aufgenommen")).toBeInTheDocument();
    await wait(1100);
    expect(router.replace).toHaveBeenCalledWith("/fahrzeuge/veh_1/fotos");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("opens the review after the last shot by replacing the camera in the history", async () => {
    render(camera({ photos: photosOf(14) }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Fahrzeugdetail");

    fireEvent.click(shutter("Foto aufnehmen"));
    await wait(20);
    expect(screen.getByText("Foto aufgenommen")).toBeInTheDocument();
    // No "choose a photo" hint while the last photo is saved and shown.
    expect(screen.queryByText(new RegExp(CHOOSE_SHOT_HINT))).toBeNull();
    await wait(1100);
    expect(router.replace).toHaveBeenCalledWith("/fahrzeuge/veh_1/fotos");
    expect(screen.getByText("Alle Pflichtfotos aufgenommen. Bitte prüfen Sie die Fotos.")).toBeInTheDocument();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("goes back to the page it was opened from instead of adding that page a second time", async () => {
    openedFrom("/fahrzeuge/veh_1/fotos");
    render(camera({ photos: photosOf(15), initialShotKey: "front_right_45", returnTo: "fotos" }));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    await wait(1100);
    expect(router.back).toHaveBeenCalledTimes(1);
    expect(router.replace).not.toHaveBeenCalled();

    // "Kamera schließen" as well.
    fireEvent.click(screen.getByRole("link", { name: "Kamera schließen" }));
    expect(router.back).toHaveBeenCalledTimes(2);
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.push).not.toHaveBeenCalled();
  });

  it("replaces the camera when it was opened from another page", () => {
    openedFrom("/fahrzeuge");
    render(camera({ photos: photosOf(4) }));
    fireEvent.click(screen.getByRole("link", { name: "Kamera schließen" }));
    expect(router.replace).toHaveBeenCalledWith("/fahrzeuge/veh_1");
    expect(router.back).not.toHaveBeenCalled();
  });
});

describe("<CameraCapture /> header", () => {
  it("keeps the remaining count and the retry action in short (landscape) viewports", () => {
    queue.items = [
      {
        id: "u_failed",
        vehicleId: VEHICLE.id,
        shotKey: "front",
        shotOrder: 2,
        title: "Vorne",
        takenAt: "2026-10-28T10:05:00.000Z",
        status: "failed",
        thumbnailUrl: null,
        error: "Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
        retryable: true,
      },
    ];
    const view = render(camera({ photos: photosOf(1) }));
    const header = view.container.querySelector("header");
    if (!header) throw new Error("no header");

    // Position, title and the full status line (portrait).
    expect(header.querySelector("p.tabular-nums")?.textContent).toBe("3 / 15");
    expect(within(header).getByRole("heading", { level: 1 })).toHaveTextContent("Vorne rechts (45°)");
    expect(within(header).getByText("2 von 15 aufgenommen · noch 13")).toHaveClass("short:hidden");

    // Short viewports: the remaining count moves next to the position …
    const compact = within(header).getByText("· noch 13");
    expect(compact).toHaveClass("hidden", "short:inline");
    expect(compact.parentElement).toBe(header.querySelector("p.tabular-nums")?.parentElement);
    // … and the retry action is never hidden – there it shows only icon and count,
    // so the progress segments keep their width.
    const retry = within(header).getByRole("button", { name: "1 nicht gespeichert · Erneut versuchen" });
    expect(retry.closest(".short\\:hidden")).toBeNull();
    expect(retry.textContent).toBe("1 nicht gespeichert · Erneut versuchen");
    expect(within(retry).getByText("nicht gespeichert · Erneut versuchen")).toHaveClass("short:hidden");
    fireEvent.click(retry);
    expect(uploads.retryAll).toHaveBeenCalledTimes(1);
  });

  it("keeps the counts and several chips on one row (icon and number), 'Prüfung aus' also in landscape", () => {
    const checkVehicle = vi.spyOn(photoChecks, "checkVehicle").mockResolvedValue();
    try {
      checks.snapshot = { availability: "offline", checks: new Map(), captures: new Map() };
      queue.items = [
        {
          id: "u_failed",
          vehicleId: VEHICLE.id,
          shotKey: "front",
          shotOrder: 2,
          title: "Vorne",
          takenAt: "2026-10-28T10:05:00.000Z",
          status: "failed",
          thumbnailUrl: null,
          error: "Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.",
          retryable: true,
        },
      ];
      const view = render(camera({ photos: photosOf(1) }));
      const header = view.container.querySelector("header");
      if (!header) throw new Error("no header");
      // Two chips: the counts get shorter and each chip shows its icon and number – its label says it in full.
      expect(within(header).getByText("2 von 15 · noch 13")).toHaveClass("short:hidden");
      const retry = within(header).getByRole("button", { name: "1 nicht gespeichert · Erneut versuchen" });
      expect(within(retry).getByText("nicht gespeichert · Erneut versuchen")).toHaveClass("sr-only");
      // The chip that explains the check is off keeps its word, also in short viewports, with a 44 px tap area.
      const off = within(header).getByRole("button", { name: "Automatische Prüfung aus · Details" });
      expect(within(off).getByText("Prüfung aus")).not.toHaveClass("short:hidden");
      expect(off).toHaveClass("h-6", "after:-top-1", "after:-bottom-4");
    } finally {
      checkVehicle.mockRestore();
    }
  });

  it("offers no retry for a file the server refused for good – it points to 'Verwerfen' instead", () => {
    queue.items = [
      {
        id: "u_refused",
        vehicleId: VEHICLE.id,
        shotKey: "front",
        shotOrder: 2,
        title: "Vorne",
        takenAt: "2026-10-28T10:05:00.000Z",
        status: "failed",
        thumbnailUrl: null,
        error: "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).",
        retryable: false,
      },
    ];
    const view = render(camera({ photos: photosOf(1) }));
    const header = view.container.querySelector("header");
    if (!header) throw new Error("no header");
    expect(within(header).queryByRole("button", { name: /Erneut versuchen/ })).toBeNull();
    const hint = within(header).getByRole("link", { name: "1 nicht speicherbar · in „Fotos überprüfen“ verwerfen" });
    expect(hint).toHaveAttribute("href", "/fahrzeuge/veh_1/fotos");
    expect(hint.textContent).toBe("1 nicht speicherbar · bitte verwerfen");
    expect(hint.closest(".short\\:hidden")).toBeNull();
  });

  it("counts only the captures a retry can still save", () => {
    const failed = (id: string, shotKey: string, retryable: boolean) => ({
      id,
      vehicleId: VEHICLE.id,
      shotKey,
      shotOrder: 2,
      title: shotKey,
      takenAt: "2026-10-28T10:05:00.000Z",
      status: "failed",
      thumbnailUrl: null,
      error: null,
      retryable,
    });
    queue.items = [failed("u1", "front", true), failed("u2", "rear", false)];
    const view = render(camera({ photos: photosOf(1) }));
    const header = view.container.querySelector("header");
    if (!header) throw new Error("no header");
    expect(within(header).getByRole("button", { name: "1 nicht gespeichert · Erneut versuchen" })).toBeInTheDocument();
  });

  it("keeps the inline position line at the line height of its text in short viewports (no overlap)", () => {
    const view = render(camera({ photos: photosOf(1) }));
    const position = view.container.querySelector("header p.tabular-nums");
    // The default 24 px line box of the wrapper pushed the title 2 px onto the progress row.
    expect(position?.parentElement).toHaveClass("short:text-sm", "short:leading-5");
  });
});

describe("<CameraCapture /> automatic check", () => {
  function check(shotKey: string, patch: Partial<PhotoCheck>): PhotoCheck {
    const shot = T.shots.find((entry) => entry.key === shotKey);
    if (!shot) throw new Error(shotKey);
    return {
      photoId: `p_${shotKey}`,
      vehicleId: VEHICLE.id,
      shotKey,
      shotOrder: shot.order,
      title: shot.title,
      takenAt: "2026-10-28T10:00:00.000Z",
      preset: "autoexperten_standard",
      status: "ok",
      message: null,
      errorCode: null,
      warnings: [],
      ...patch,
    };
  }

  function setChecks(availability: PhotoChecksSnapshot["availability"], list: PhotoCheck[]) {
    checks.snapshot = {
      availability,
      checks: new Map(list.map((entry) => [entry.photoId, entry])),
      captures: new Map(),
    };
  }

  const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";

  it("shows each shot's check in the progress and jumps to a rejected shot for the retake", () => {
    setChecks("on", [
      check("front_left_45", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
      check("front", { status: "ok" }),
      check("front_right_45", { status: "checking" }),
    ]);
    render(camera({ photos: photosOf(3) }));
    expect(screen.getByRole("button", { name: `1. Vorne links (45°) – Bitte neu aufnehmen: ${TOO_SMALL}` })).toHaveAttribute(
      "data-check",
      "retake",
    );
    expect(screen.getByRole("button", { name: "2. Vorne – Geprüft – passt" })).toHaveAttribute("data-check", "ok");
    expect(screen.getByRole("button", { name: "3. Vorne rechts (45°) – Wird geprüft…" })).toHaveAttribute(
      "data-check",
      "checking",
    );
    // Current shot, progress and remaining photos stay visible; the shutter is free.
    expect(heading()).toHaveTextContent("Linke Fahrzeugseite");
    expect(screen.getByText("3 von 15 aufgenommen · noch 12")).toBeInTheDocument();
    expect(shutter("Foto aufnehmen")).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "1 Foto neu aufnehmen · zum ersten springen" }));
    expect(heading()).toHaveTextContent("Vorne links (45°)");
    expect(screen.getByText(TOO_SMALL)).toBeInTheDocument();
    expect(shutter("Foto wiederholen")).toBeEnabled();
  });

  it("never shows the result of a photo that a newer capture replaces", () => {
    setChecks("on", [check("front_left_45", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" })]);
    queue.items = [
      {
        id: "u_retake",
        vehicleId: VEHICLE.id,
        shotKey: "front_left_45",
        shotOrder: 1,
        title: "Vorne links (45°)",
        takenAt: "2026-10-28T10:05:00.000Z",
        status: "uploading",
        thumbnailUrl: null,
        error: null,
        retryable: true,
      },
    ];
    render(camera({ photos: photosOf(1) }));
    expect(screen.getByRole("button", { name: "1. Vorne links (45°) – Wird geprüft…" })).toBeInTheDocument();
    expect(screen.queryByText(TOO_SMALL)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /neu aufnehmen · zum ersten springen/ })).not.toBeInTheDocument();
  });

  it("shows a short banner when a check finishes", async () => {
    let settled: ((entry: PhotoCheck) => void) | null = null;
    const spy = vi.spyOn(photoChecks, "onSettled").mockImplementation((listener) => {
      settled = listener;
      return () => {};
    });
    try {
      setChecks("on", []);
      const view = render(camera({ photos: photosOf(2) }));
      const header = view.container.querySelector("header") as HTMLElement;
      act(() => settled?.(check("front", { status: "ok" })));
      // In the header, never on the framing guide – with the wording of every screen.
      expect(within(header).getByText("02 Vorne · Geprüft – passt")).toBeInTheDocument();
      // Choosing a shot closes the banner, so that shot's own state shows.
      fireEvent.click(screen.getByRole("button", { name: /^2\. Vorne – / }));
      expect(screen.queryByText("02 Vorne · Geprüft – passt")).not.toBeInTheDocument();
      act(() =>
        settled?.(check("front_left_45", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" })),
      );
      const alert = screen.getByRole("alert");
      expect(header).toContainElement(alert);
      expect(alert).toHaveTextContent(`01 Vorne links (45°) · Bitte neu aufnehmen: ${TOO_SMALL}`);
      // One line; tapping it opens the full text with the jump to the rejected shot.
      const line = within(alert).getByRole("button", { name: /01 Vorne links/ });
      expect(line).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(line);
      expect(line).toHaveAttribute("aria-expanded", "true");
      fireEvent.click(within(header).getByRole("button", { name: "Jetzt wiederholen" }));
      expect(heading()).toHaveTextContent("Vorne links (45°)");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(within(header).queryByRole("button", { name: "Jetzt wiederholen" })).not.toBeInTheDocument();
      // A check of another vehicle says nothing here.
      act(() => settled?.({ ...check("front", { status: "ok" }), vehicleId: "veh_other" }));
      expect(screen.queryByText("02 Vorne · Geprüft – passt")).not.toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it("works as before with one discreet hint when the check is off (demo: code entry in the camera)", async () => {
    const checkVehicle = vi.spyOn(photoChecks, "checkVehicle").mockResolvedValue();
    try {
      setChecks("access_code", []);
      render(camera({ photos: photosOf(2) }));
      expect(screen.getByRole("button", { name: "2. Vorne – aufgenommen" })).not.toHaveAttribute("data-check", "checking");
      expect(shutter("Foto aufnehmen")).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Automatische Prüfung aus · Details" }));
      const dialog = screen.getByRole("dialog", { name: "Automatische Prüfung aus" });
      expect(dialog).toHaveTextContent("Zugangscode");
      fireEvent.change(within(dialog).getByLabelText("Zugangscode für die Bildbearbeitung"), { target: { value: "geheim" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Speichern" }));
      expect(window.localStorage.getItem("ae-photo.processing-access-code")).toBe("geheim");
      expect(checkVehicle).toHaveBeenCalledWith(VEHICLE.id);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    } finally {
      checkVehicle.mockRestore();
      window.localStorage.removeItem("ae-photo.processing-access-code");
    }
  });
  it("checks the photos taken meanwhile when the check comes on again (connection back)", () => {
    const checkVehicle = vi.spyOn(photoChecks, "checkVehicle").mockResolvedValue();
    try {
      setChecks("offline", []);
      const { rerender } = render(camera({ photos: photosOf(2) }));
      expect(checkVehicle).not.toHaveBeenCalled();
      setChecks("on", []);
      rerender(camera({ photos: photosOf(2) }));
      expect(checkVehicle).toHaveBeenCalledTimes(1);
      expect(checkVehicle).toHaveBeenCalledWith(VEHICLE.id);
    } finally {
      checkVehicle.mockRestore();
    }
  });

  it("shows the current shot's state as one line in the header, never on the viewfinder", () => {
    setChecks("on", [check("front_left_45", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" })]);
    const view = render(camera({ photos: photosOf(15), initialShotKey: "front_left_45" }));
    const header = view.container.querySelector("header") as HTMLElement;
    const viewfinder = screen.getByLabelText("Live-Kamerabild").parentElement as HTMLElement;
    const line = within(header).getByRole("button", { name: /^Bitte neu aufnehmen:\s*Fahrzeug im Originalfoto zu klein/ });
    expect(line.querySelector(".truncate")).not.toBeNull();
    expect(within(viewfinder).queryByText(TOO_SMALL)).toBeNull();
    // Current shot, progress, remaining count and shutter stay.
    expect(heading()).toHaveTextContent("Vorne links (45°)");
    expect(within(header).getByText("15 von 15 aufgenommen · noch 0")).toBeInTheDocument();
    expect(shutter("Foto wiederholen")).toBeEnabled();
    // Tapping opens the full reason below the header; closing it hides it again.
    fireEvent.click(line);
    expect(within(header).getByRole("button", { name: "Hinweis schließen" })).toBeInTheDocument();
    fireEvent.click(within(header).getByRole("button", { name: "Hinweis schließen" }));
    expect(within(header).queryByRole("button", { name: "Hinweis schließen" })).toBeNull();
  });

  it("shows interior and detail shots as saved – never as passed", () => {
    const interior = T.shots.find((shot) => shot.category === "interior");
    if (!interior) throw new Error("no interior shot");
    setChecks("on", [check(interior.key, { status: "ok" }), check("front", { status: "ok" })]);
    const view = render(camera({ photos: photosOf(15), initialShotKey: interior.key }));
    const header = view.container.querySelector("header") as HTMLElement;
    expect(screen.getByRole("button", { name: `${interior.order}. ${interior.title} – Gespeichert` })).toHaveAttribute(
      "data-check",
      "saved",
    );
    expect(within(header).getByRole("button", { name: "Gespeichert" })).toHaveAttribute("data-tone", "saved");
    expect(within(header).queryByText(/passt/)).toBeNull();
    expect(screen.getByRole("button", { name: "2. Vorne – Geprüft – passt" })).toHaveAttribute("data-check", "ok");
  });

  it("says a result with a processor hint should be looked at – in one wording", () => {
    const hint = { code: "vehicle_part_uncertain", message: "Ein Fahrzeugteil ist unsicher – bitte prüfen." };
    setChecks("on", [check("front", { status: "ok", warnings: [hint] })]);
    const view = render(camera({ photos: photosOf(15), initialShotKey: "front" }));
    const header = view.container.querySelector("header") as HTMLElement;
    expect(screen.getByRole("button", { name: `2. Vorne – Geprüft – bitte Ergebnis ansehen: ${hint.message}` })).toHaveAttribute(
      "data-check",
      "hint",
    );
    // Label and hint read as one sentence: "<label>: <hint>".
    expect(within(header).getByRole("button", { name: `Geprüft – bitte Ergebnis ansehen: ${hint.message}` })).toBeInTheDocument();
    expect(within(header).queryByText(/passt/)).toBeNull();
  });

  it("shows a finished check with a processor hint in amber – and nothing for an interior shot", () => {
    let settled: ((entry: PhotoCheck) => void) | null = null;
    const spy = vi.spyOn(photoChecks, "onSettled").mockImplementation((listener) => {
      settled = listener;
      return () => {};
    });
    try {
      setChecks("on", []);
      const view = render(camera({ photos: photosOf(15), initialShotKey: "left_side" }));
      const header = view.container.querySelector("header") as HTMLElement;
      const hint = { code: "vehicle_part_uncertain", message: "Ein Fahrzeugteil ist unsicher – bitte prüfen." };
      act(() => settled?.(check("front", { status: "ok", warnings: [hint] })));
      const line = within(header).getByRole("button", { name: `02 Vorne · Geprüft – bitte Ergebnis ansehen: ${hint.message}` });
      expect(line).toHaveAttribute("data-tone", "hint");
      fireEvent.click(screen.getByRole("button", { name: /^4\. Linke Fahrzeugseite – / }));
      act(() => settled?.(check("cockpit", { status: "ok" })));
      expect(within(header).queryByText(/Cockpit/)).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });

  it("goes on to the next red shot after a retake instead of leaving – and leaves when none is red", async () => {
    setChecks("on", [
      check("front", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
      check("rear", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
    ]);
    const view = render(camera({ photos: photosOf(15) }));
    const { rerender } = view;
    const header = view.container.querySelector("header") as HTMLElement;
    // Every photo is there: the employee chooses the red shot to retake.
    fireEvent.click(screen.getByRole("button", { name: /^7\. Hinten – Bitte neu aufnehmen/ }));
    expect(heading()).toHaveTextContent("Hinten");
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    await wait(1100);
    // The next red shot after "Hinten" (wrapping around): "Vorne". The rejection of
    // the photo just replaced does not count.
    expect(heading()).toHaveTextContent(/^Vorne$/);
    // Said in the header's status line – no toast over the framing guide.
    const notice = within(header).getByRole("alert");
    expect(notice).toHaveTextContent("Noch 1 Foto neu aufnehmen");
    expect(screen.getAllByText("Noch 1 Foto neu aufnehmen")).toHaveLength(1);
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.back).not.toHaveBeenCalled();

    // "Hinten" is being checked, "Vorne" is retaken: none is red any more – on to the review.
    setChecks("on", [
      check("front", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
      check("rear", { photoId: "u_rear", status: "checking", takenAt: "2026-10-28T10:10:00.000Z" }),
    ]);
    rerender(camera({ photos: photosOf(15) }));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    await wait(1100);
    expect(router.replace).toHaveBeenCalledWith("/fahrzeuge/veh_1/fotos");
    expect(screen.getByText("Alle Pflichtfotos aufgenommen. Bitte prüfen Sie die Fotos.")).toBeInTheDocument();
  });

  it("counts the shot just retaken when its new photo was already rejected", async () => {
    setChecks("on", [
      check("front", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
      check("rear", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
    ]);
    const view = render(camera({ photos: photosOf(15) }));
    const header = view.container.querySelector("header") as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: /^7\. Hinten – Bitte neu aufnehmen/ }));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    // A fast processor rejects the new photo of "Hinten" before the preview ends.
    setChecks("on", [
      check("front", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" }),
      check("rear", { photoId: "u_rear", status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small", takenAt: "2030-01-01T00:00:00.000Z" }),
    ]);
    view.rerender(camera({ photos: photosOf(15) }));
    await wait(1100);
    expect(heading()).toHaveTextContent(/^Vorne$/);
    expect(within(header).getByRole("alert")).toHaveTextContent("Noch 2 Fotos neu aufnehmen");
    expect(within(header).getByRole("button", { name: "2 Fotos neu aufnehmen · zum ersten springen" })).toBeInTheDocument();
  });

  it("stays on the shot just retaken when only its new photo is red", async () => {
    setChecks("on", [check("rear", { status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small" })]);
    const view = render(camera({ photos: photosOf(15) }));
    const header = view.container.querySelector("header") as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: /^7\. Hinten – Bitte neu aufnehmen/ }));
    fireEvent.click(shutter("Foto wiederholen"));
    await wait(20);
    setChecks("on", [
      check("rear", { photoId: "u_rear", status: "retake", message: TOO_SMALL, errorCode: "vehicle_too_small", takenAt: "2030-01-01T00:00:00.000Z" }),
    ]);
    view.rerender(camera({ photos: photosOf(15) }));
    await wait(1100);
    expect(heading()).toHaveTextContent(/^Hinten$/);
    expect(within(header).getByRole("alert")).toHaveTextContent("Noch 1 Foto neu aufnehmen");
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("checks the photos when the camera opens and the check comes on for the first time (after a reload)", () => {
    const checkVehicle = vi.spyOn(photoChecks, "checkVehicle").mockResolvedValue();
    try {
      setChecks("unknown", []);
      const { rerender } = render(camera({ photos: photosOf(2) }));
      expect(checkVehicle).not.toHaveBeenCalled();
      setChecks("on", []);
      rerender(camera({ photos: photosOf(2) }));
      expect(checkVehicle).toHaveBeenCalledWith(VEHICLE.id);
    } finally {
      checkVehicle.mockRestore();
    }
  });
});
