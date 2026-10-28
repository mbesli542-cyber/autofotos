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
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { CameraCapture } from "./CameraCapture";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }));
const uploads = vi.hoisted(() => ({ enqueue: vi.fn(), retryAll: vi.fn() }));
const queue = vi.hoisted(() => ({ items: [] as unknown[] }));

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
  router.push.mockReset();
  router.replace.mockReset();
  router.back.mockReset();
  uploads.enqueue.mockReset();
  uploads.enqueue.mockImplementation(async (input: NewPendingUpload) => ({ id: "u_new", ...input, status: "queued" }));
  uploads.retryAll.mockReset();
  queue.items = [];
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
