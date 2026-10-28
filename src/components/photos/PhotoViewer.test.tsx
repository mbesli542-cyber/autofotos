import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { Activity, useState } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { installDialogPolyfill } from "@/components/ui/dialog-test-polyfill";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import { AppError } from "@/lib/errors";
import type { UploadItem } from "@/lib/offline/upload-queue";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { PhotoViewer } from "./PhotoViewer";

beforeAll(() => {
  installDialogPolyfill();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const savedPhoto: VehiclePhotoWithUrls = {
  id: "photo-rear",
  vehicleId: "v1",
  shotKey: "rear",
  shotOrder: 7,
  title: "Hinten",
  originalStoragePath: "v1/rear/photo-rear.jpg",
  processedStoragePath: null,
  processedPreset: null,
  processingWarnings: [],
  thumbnailStoragePath: null,
  width: 4000,
  height: 3000,
  takenAt: "2026-10-28T10:00:00.000Z",
  createdAt: "2026-10-28T10:00:00.000Z",
  updatedAt: "2026-10-28T10:00:00.000Z",
  urls: { original: "/rear.jpg", thumbnail: "/rear.jpg", processed: null },
};

function pending(overrides: Partial<UploadItem> = {}): UploadItem {
  return {
    id: "upload-1",
    vehicleId: "v1",
    shotKey: "rear",
    shotOrder: 7,
    title: "Hinten",
    takenAt: "2026-10-28T10:05:00.000Z",
    status: "failed",
    thumbnailUrl: "blob:retake",
    error: "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.",
    retryable: true,
    ...overrides,
  };
}

function renderViewer({
  photos = [savedPhoto],
  uploads = [] as UploadItem[],
  onDelete = vi.fn(async () => {}),
  onRetryUpload = vi.fn(),
} = {}) {
  const slots = buildPhotoSlots(T, photos, uploads).required;
  render(
    <ToastProvider>
      <PhotoViewer
        slots={slots}
        selectedKey="rear"
        onNavigate={() => {}}
        onClose={() => {}}
        onRetake={() => {}}
        onDelete={onDelete}
        onRetryUpload={onRetryUpload}
      />
    </ToastProvider>,
  );
  const dialog = screen.getByRole("dialog");
  const buttons = () => within(dialog).getAllByRole("button").map((button) => button.textContent?.trim());
  return { dialog, buttons, onDelete, onRetryUpload };
}

describe("<PhotoViewer /> – 'Foto löschen' fails", () => {
  it("catches the error (no unhandled rejection) and keeps the confirmation usable", async () => {
    const unhandled: unknown[] = [];
    const track = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", track);
    try {
      // Like the screens: the error of the delete is passed on.
      const onDelete = vi.fn(async () => {
        throw new AppError("network");
      });
      const { dialog } = renderViewer({ onDelete });
      fireEvent.click(within(dialog).getByRole("button", { name: "Foto löschen" }));
      fireEvent.click(within(dialog).getByRole("button", { name: "Löschen" }));
      await act(() => new Promise((resolve) => setTimeout(resolve, 30)));

      expect(onDelete).toHaveBeenCalledOnce();
      expect(unhandled).toEqual([]);
      expect(within(dialog).getByText("Foto wirklich löschen?")).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Löschen" })).toBeEnabled();
      expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeEnabled();
      // Said inside the viewer: a toast would be hidden behind the modal dialog.
      expect(within(dialog).getByRole("alert")).toHaveTextContent(
        "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.",
      );
    } finally {
      process.off("unhandledRejection", track);
    }
  });
});

describe("<PhotoViewer /> – a capture that could not be saved", () => {
  it("offers 'Erneut speichern' and 'Verwerfen' for a failed upload", () => {
    const { buttons } = renderViewer({ uploads: [pending()] });
    expect(buttons()).toEqual(expect.arrayContaining(["Erneut speichern", "Foto wiederholen", "Verwerfen"]));
    expect(buttons()).not.toContain("Foto löschen");
  });

  it("offers only 'Verwerfen' for a file the server refused for good", () => {
    const message = "Dieses Bildformat wird nicht unterstützt (JPG, PNG, WebP oder HEIC).";
    const { dialog, buttons } = renderViewer({ uploads: [pending({ retryable: false, error: message })] });
    expect(within(dialog).getByText(message)).toBeInTheDocument();
    expect(buttons()).toEqual(expect.arrayContaining(["Verwerfen", "Foto wiederholen"]));
    expect(buttons()).not.toContain("Erneut speichern");
  });

  it("asks before discarding and then removes the capture from the queue", async () => {
    const discard = vi.spyOn(getAppServices().uploads, "discard").mockResolvedValue();
    const { dialog } = renderViewer({ uploads: [pending({ retryable: false })] });

    fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    expect(within(dialog).getByText("Foto verwerfen?")).toBeInTheDocument();
    expect(within(dialog).getByText(/Das zuletzt gespeicherte Foto bleibt erhalten/)).toBeInTheDocument();
    expect(discard).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    });
    expect(discard).toHaveBeenCalledWith("upload-1");
    expect(within(dialog).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
  });

  it("can cancel discarding", () => {
    const discard = vi.spyOn(getAppServices().uploads, "discard").mockResolvedValue();
    const { dialog } = renderViewer({ uploads: [pending()] });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    expect(within(dialog).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
    expect(discard).not.toHaveBeenCalled();
  });

  it("does not offer 'Verwerfen' while the capture is still being saved", () => {
    const { buttons } = renderViewer({ uploads: [pending({ status: "uploading", error: null })] });
    expect(buttons()).not.toContain("Verwerfen");
  });

  it("shows the newer saved photo – with 'Foto löschen' – instead of an older failed capture", () => {
    const { dialog, buttons } = renderViewer({
      uploads: [pending({ takenAt: "2026-10-28T09:55:00.000Z", retryable: false })],
    });
    expect(within(dialog).getByRole("img")).toHaveAttribute("src", "/rear.jpg");
    expect(buttons()).toContain("Foto löschen");
    expect(buttons()).not.toContain("Verwerfen");
  });
});

describe("<PhotoViewer /> – a question belongs to the photo it was asked for", () => {
  /** Like the screens: tapping a tile opens the viewer, closing clears the selection. */
  function Screen({ photos = [] as VehiclePhotoWithUrls[], uploads = [] as UploadItem[] }) {
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    const slots = buildPhotoSlots(T, photos, uploads).required;
    return (
      <ToastProvider>
        {slots.map((slot) => (
          <button key={slot.key} type="button" onClick={() => setSelectedKey(slot.key)}>
            {`Öffnen: ${slot.key}`}
          </button>
        ))}
        <PhotoViewer
          slots={slots}
          selectedKey={selectedKey}
          onNavigate={setSelectedKey}
          onClose={() => setSelectedKey(null)}
          onRetake={() => {}}
          onDelete={async () => {}}
          onRetryUpload={() => {}}
        />
      </ToastProvider>
    );
  }

  const dialog = () => screen.getByRole("dialog");
  const open = (key: string) => fireEvent.click(screen.getByRole("button", { name: `Öffnen: ${key}` }));
  const close = () => fireEvent.click(within(dialog()).getByRole("button", { name: "Großansicht schließen" }));
  const failed = (shotKey: string, shotOrder: number) =>
    pending({ id: `upload-${shotKey}`, shotKey, shotOrder, retryable: false, thumbnailUrl: `blob:${shotKey}` });

  it("closing the viewer dismisses 'Foto verwerfen?' – no other capture can be discarded with one tap", () => {
    const discard = vi.spyOn(getAppServices().uploads, "discard").mockResolvedValue();
    render(<Screen photos={[]} uploads={[failed("front", 2), failed("rear", 7)]} />);

    open("front");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Verwerfen" }));
    expect(within(dialog()).getByText("Foto verwerfen?")).toBeInTheDocument();
    close();

    open("rear");
    expect(within(dialog()).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
    close();
    open("front");
    expect(within(dialog()).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
    expect(discard).not.toHaveBeenCalled();
  });

  it("showing another photo or reopening the viewer dismisses the questions", () => {
    render(<Screen photos={[savedPhoto]} uploads={[failed("rear_right_45", 8)]} />);

    open("rear");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Foto löschen" }));
    expect(within(dialog()).getByText("Foto wirklich löschen?")).toBeInTheDocument();
    close();
    open("rear");
    expect(within(dialog()).queryByText("Foto wirklich löschen?")).not.toBeInTheDocument();

    fireEvent.click(within(dialog()).getByRole("button", { name: /^Nächstes Foto/ }));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Verwerfen" }));
    expect(within(dialog()).getByText("Foto verwerfen?")).toBeInTheDocument();
    fireEvent.click(within(dialog()).getByRole("button", { name: /^Vorheriges Foto/ }));
    expect(within(dialog()).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
    fireEvent.click(within(dialog()).getByRole("button", { name: /^Nächstes Foto/ }));
    expect(within(dialog()).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
  });
});

describe("<PhotoViewer /> – a question stays with its photo or capture while the route is hidden", () => {
  /** A screen route (kept mounted, hidden with <Activity>) whose viewer stays on "Hinten". */
  function page(photos: VehiclePhotoWithUrls[], uploads: UploadItem[], mode: "visible" | "hidden" = "visible") {
    return (
      <ToastProvider>
        <Activity mode={mode}>
          <PhotoViewer
            slots={buildPhotoSlots(T, photos, uploads).required}
            selectedKey="rear"
            onNavigate={() => {}}
            onClose={() => {}}
            onRetake={() => {}}
            onDelete={async () => {}}
            onRetryUpload={() => {}}
          />
        </Activity>
      </ToastProvider>
    );
  }

  const dialog = () => screen.getByRole("dialog", { hidden: true });

  it("never asks 'Foto verwerfen?' for another capture than the one it was asked for", () => {
    const discard = vi.spyOn(getAppServices().uploads, "discard").mockResolvedValue();
    const captureA = pending({ id: "upload-a", retryable: false, thumbnailUrl: "blob:a" });
    const view = render(page([savedPhoto], [captureA]));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Verwerfen" }));
    expect(within(dialog()).getByText("Foto verwerfen?")).toBeInTheDocument();

    // Back (route hidden): meanwhile A was saved and a newer capture B of "Hinten" failed.
    view.rerender(page([savedPhoto], [captureA], "hidden"));
    const captureB = pending({ id: "upload-b", takenAt: "2026-10-28T10:10:00.000Z", retryable: false, thumbnailUrl: "blob:b" });
    view.rerender(page([savedPhoto], [captureB], "hidden"));
    view.rerender(page([savedPhoto], [captureB]));

    expect(within(dialog()).getByRole("img")).toHaveAttribute("src", "blob:b");
    expect(within(dialog()).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
    expect(discard).not.toHaveBeenCalled();
  });

  it("drops 'Foto wirklich löschen?' once the shot has a newer photo", () => {
    const view = render(page([savedPhoto], []));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Foto löschen" }));
    expect(within(dialog()).getByText("Foto wirklich löschen?")).toBeInTheDocument();

    // A retake replaced the photo (e.g. saved on another device, loaded by a reload).
    const retake = { ...savedPhoto, id: "photo-rear-2", takenAt: "2026-10-28T11:00:00.000Z" };
    view.rerender(page([retake], []));
    expect(within(dialog()).queryByText("Foto wirklich löschen?")).not.toBeInTheDocument();
  });

  it("ends an unanswered question when the route is hidden", () => {
    const view = render(page([savedPhoto], []));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Foto löschen" }));
    view.rerender(page([savedPhoto], [], "hidden"));
    view.rerender(page([savedPhoto], []));
    expect(within(dialog()).queryByText("Foto wirklich löschen?")).not.toBeInTheDocument();
  });
});

describe("<PhotoViewer /> – 'Verwerfen' fails", () => {
  it("keeps the question and says so when the capture could not be removed", async () => {
    vi.spyOn(getAppServices().uploads, "discard").mockRejectedValue(new DOMException("Datenbankfehler", "UnknownError"));
    const { dialog } = renderViewer({ uploads: [pending({ retryable: false })] });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    });
    expect(within(dialog).getByText("Foto verwerfen?")).toBeInTheDocument();
    // Inside the viewer: a toast would be hidden behind the modal dialog.
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Das Foto konnte nicht verworfen werden. Bitte versuchen Sie es erneut.",
    );
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("says it only in the question it belongs to – and a new question starts without it", async () => {
    const discard = vi
      .spyOn(getAppServices().uploads, "discard")
      .mockRejectedValueOnce(new DOMException("Datenbankfehler", "UnknownError"))
      .mockResolvedValueOnce();
    const { dialog } = renderViewer({ uploads: [pending({ retryable: false })] });
    fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    });
    expect(within(dialog).getByRole("alert")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    expect(within(dialog).getByText("Foto verwerfen?")).toBeInTheDocument();
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();

    // The second attempt works.
    await act(async () => {
      fireEvent.click(within(dialog).getByRole("button", { name: "Verwerfen" }));
    });
    expect(discard).toHaveBeenCalledTimes(2);
    expect(within(dialog).queryByText("Foto verwerfen?")).not.toBeInTheDocument();
  });
});

describe("<PhotoViewer /> – layout (measured in Chromium; jsdom has no layout)", () => {
  it("keeps the photo at least 40 % high and lets details and actions scroll – next to the photo on short viewports", () => {
    const { dialog } = renderViewer();
    // Phones: full screen. Tablets/desktop: a card of fixed height (the photo area can
    // only shrink – it never overlaps the hints). Short viewports: full screen again.
    expect(dialog).toHaveClass("h-dvh", "sm:h-[min(94dvh,66rem)]", "short:h-dvh", "short:w-screen", "short:m-0");
    expect(dialog.className).not.toMatch(/sm:h-auto|sm:max-h-/);
    const image = within(dialog).getByRole("img");
    expect(image).toHaveClass("max-h-full", "w-full", "object-contain");
    expect(image.className).not.toMatch(/max-h-\[/);
    const imageArea = image.parentElement as HTMLElement;
    expect(imageArea).toHaveClass("min-h-[40%]", "flex-1", "short:min-h-0");
    const body = imageArea.parentElement as HTMLElement;
    expect(body).toHaveClass("flex", "flex-col", "short:flex-row", "min-h-0", "flex-1");
    const details = dialog.querySelector("footer") as HTMLElement;
    expect(details.parentElement).toBe(body);
    expect(details).toHaveClass("min-h-0", "overflow-y-auto", "short:w-72", "short:shrink-0", "short:flex", "short:flex-col");
    // The actions stack in the narrow side panel – right below the version switch,
    // the capture details (and hints) after them, so the actions never need scrolling.
    const actions = within(dialog).getByRole("button", { name: "Foto löschen" }).parentElement as HTMLElement;
    expect(actions).toHaveClass("grid-cols-2", "short:grid-cols-1");
    expect(actions.className).not.toMatch(/short:order-/);
    expect(within(dialog).getByText(/^Aufgenommen am/)).toHaveClass("short:order-1");
  });

  it("puts the hints after the actions in the short side panel", () => {
    const processed: VehiclePhotoWithUrls = {
      ...savedPhoto,
      processedStoragePath: "v1/autoexperten_standard/rear/photo-rear.jpg",
      processedPreset: "autoexperten_standard",
      processingWarnings: [{ code: "mask_uncertain", message: "Die Freistellung ist unsicher – bitte das Ergebnis prüfen." }],
      urls: { original: "/rear.jpg", thumbnail: "/rear.jpg", processed: "/rear-processed.jpg" },
    };
    render(
      <ToastProvider>
        <PhotoViewer
          slots={buildPhotoSlots(T, [processed], []).required}
          selectedKey="rear"
          defaultVariant="processed"
          onNavigate={() => {}}
          onClose={() => {}}
          onRetake={() => {}}
        />
      </ToastProvider>,
    );
    const hint = within(screen.getByRole("dialog")).getByText(/Die Freistellung ist unsicher/);
    expect(hint.closest("p")?.parentElement).toHaveClass("short:order-1");
  });
});

describe("<PhotoViewer /> – hints of the processor on the processed version", () => {
  const processed: VehiclePhotoWithUrls = {
    ...savedPhoto,
    processedStoragePath: "v1/autoexperten_standard/rear/photo-rear.jpg",
    processedPreset: "autoexperten_standard",
    processingWarnings: [
      {
        code: "background_object_removed",
        message:
          "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
      },
      { code: "low_resolution", message: "Das Fahrzeug musste vergrößert werden – für beste Qualität näher heran oder mit höherer Auflösung fotografieren." },
    ],
    urls: { original: "/rear.jpg", thumbnail: "/rear.jpg", processed: "/rear-processed.jpg" },
  };

  function viewer(variant: "original" | "processed") {
    return (
      <ToastProvider>
        <PhotoViewer
          slots={buildPhotoSlots(T, [processed], []).required}
          selectedKey="rear"
          defaultVariant={variant}
          onNavigate={() => {}}
          onClose={() => {}}
          onRetake={() => {}}
        />
      </ToastProvider>
    );
  }

  it("shows them with 'Bearbeitet' (amber: to check, subtle: information) – not with the original", () => {
    const view = render(viewer("processed"));
    const dialog = screen.getByRole("dialog");
    const check = within(dialog).getByText(/wurde entfernt – bitte das Ergebnis prüfen/);
    expect(check.closest("p")).toHaveClass("text-ae-warning");
    expect(within(dialog).getByText(/musste vergrößert werden/).closest("p")).toHaveClass("text-ae-subtle");
    expect(within(dialog).getByText("Bearbeitet · bitte prüfen")).toBeInTheDocument();

    view.unmount();
    render(viewer("original"));
    expect(within(screen.getByRole("dialog")).queryByText(/bitte das Ergebnis prüfen/)).not.toBeInTheDocument();
  });
});
