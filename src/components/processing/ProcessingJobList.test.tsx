import { fireEvent, render, screen } from "@testing-library/react";
import { Activity, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PhotoRunState } from "@/lib/processing/processing-runs";
import { ProcessingJobList, type JobRetakeAction } from "./ProcessingJobList";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

function item(overrides: Partial<PhotoRunState>): PhotoRunState {
  return {
    photoId: "p1",
    shotKey: "front_left_45",
    title: "Vorne links (45°)",
    shotOrder: 1,
    treatment: "showroom",
    status: "complete",
    progress: 1,
    error: null,
    errorCode: null,
    warnings: [],
    ...overrides,
  };
}

const MIRRORED = {
  code: "plate_mirrored",
  message:
    "Das Foto zeigt das Fahrzeug von der anderen Seite – es wurde der Showroom-Hintergrund „Vorne rechts (45°)“ verwendet. Bitte die Aufnahmeposition prüfen.",
};
const REMOVED = {
  code: "background_object_removed",
  message:
    "Ein Gegenstand hinter dem Fahrzeug (z. B. Leitkegel, Pfosten oder Schild) ragte in die Fahrzeugkontur und wurde entfernt – bitte das Ergebnis prüfen.",
};
const UPSCALED = {
  code: "low_resolution",
  message: "Das Fahrzeug musste vergrößert werden – für beste Qualität näher heran oder mit höherer Auflösung fotografieren.",
};

const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";

/** jsdom has no scrolling – records which row was scrolled into view. */
const scrolledRows: { shotKey: string | undefined; options: unknown }[] = [];

beforeEach(() => {
  Element.prototype.scrollIntoView = function (this: HTMLElement, options?: unknown) {
    scrolledRows.push({ shotKey: this.dataset.shotKey, options });
  };
});

afterEach(() => {
  scrolledRows.length = 0;
  delete (Element.prototype as Partial<Element>).scrollIntoView;
});

describe("<ProcessingJobList />", () => {
  it("shows the processor's message and a retake link for rejected shots", () => {
    const rejected = item({
      photoId: "p2",
      shotKey: "front",
      title: "Front",
      shotOrder: 2,
      status: "failed",
      error: TOO_SMALL,
      errorCode: "vehicle_too_small",
    });
    render(
      <ProcessingJobList
        items={[item({}), rejected]}
        retakeAction={(entry) =>
          entry.shotKey === "front" ? { kind: "retake", href: "/fahrzeuge/v1/kamera?shot=front&zurueck=bearbeiten" } : null
        }
      />,
    );
    expect(screen.getByText(TOO_SMALL)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Foto neu aufnehmen: Front" })).toHaveAttribute(
      "href",
      "/fahrzeuge/v1/kamera?shot=front&zurueck=bearbeiten",
    );
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("offers to process the new photo once it was saved", () => {
    const onProcess = vi.fn();
    const action: JobRetakeAction = { kind: "ready", onProcess };
    render(
      <ProcessingJobList
        items={[item({ status: "failed", error: TOO_SMALL, errorCode: "vehicle_too_small" })]}
        retakeAction={() => action}
      />,
    );
    // The old rejection no longer applies to the new photo.
    expect(screen.queryByText(TOO_SMALL)).not.toBeInTheDocument();
    expect(screen.getByText("Neues Foto aufgenommen.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Neues Foto bearbeiten: Vorne links (45°)" }));
    expect(onProcess).toHaveBeenCalledOnce();
  });

  it("offers the new photo of a shot that was already processed (retake saved during the run)", () => {
    const onProcess = vi.fn();
    render(<ProcessingJobList items={[item({})]} retakeAction={() => ({ kind: "ready", onProcess })} />);
    expect(screen.queryByText("Fertig")).not.toBeInTheDocument();
    expect(screen.getByText("Neues Foto")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Neues Foto bearbeiten: Vorne links (45°)" }));
    expect(onProcess).toHaveBeenCalledOnce();
  });

  it("brings a row that just got a new photo to process to the middle of the screen – once", () => {
    const rows = [
      item({ photoId: "p1", shotKey: "front_left_45" }),
      item({ photoId: "p2", shotKey: "right_side", title: "Rechte Seite", shotOrder: 5, status: "failed", error: TOO_SMALL, errorCode: "vehicle_too_small" }),
    ];
    /** The processing route with the shots that have a saved new photo. */
    const page = (ready: string[], visible = true) => (
      <Activity mode={visible ? "visible" : "hidden"}>
        <ProcessingJobList
          items={rows}
          retakeAction={(entry): JobRetakeAction | null =>
            ready.includes(entry.shotKey)
              ? { kind: "ready", onProcess: vi.fn() }
              : entry.status === "failed"
                ? { kind: "retake", href: "/fahrzeuge/v1/kamera?shot=right_side&zurueck=bearbeiten" }
                : null
          }
        />
      </Activity>
    );
    const { rerender } = render(page([]));
    expect(scrolledRows).toEqual([]);

    // Back from the camera: the new photo of row 05 is saved.
    rerender(page(["right_side"]));
    expect(scrolledRows).toEqual([{ shotKey: "right_side", options: { block: "center" } }]);

    // Not again on later updates or each time the page is shown again …
    rerender(page(["right_side"]));
    rerender(page(["right_side"], false));
    rerender(page(["right_side"]));
    expect(scrolledRows).toHaveLength(1);

    // … only for the next row that gets a new photo.
    rerender(page(["front_left_45", "right_side"]));
    expect(scrolledRows.map((entry) => entry.shotKey)).toEqual(["right_side", "front_left_45"]);
  });

  it("lets a photo that failed for a temporary reason be processed again", () => {
    const onRetry = vi.fn();
    const message = "Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.";
    render(
      <ProcessingJobList items={[item({ status: "failed", error: message })]} retakeAction={() => ({ kind: "retry", onRetry })} />,
    );
    // The reason stays visible.
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("Fehlgeschlagen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen: Vorne links (45°)" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("shows a saving new photo and lets a failed upload be retried", () => {
    const onRetryUpload = vi.fn();
    const failed = item({ status: "failed", error: TOO_SMALL, errorCode: "vehicle_too_small" });
    const { rerender } = render(
      <ProcessingJobList items={[failed]} retakeAction={() => ({ kind: "saving", failed: false, onRetryUpload })} />,
    );
    expect(screen.getByText("Neues Foto wird gespeichert…")).toBeInTheDocument();

    rerender(<ProcessingJobList items={[failed]} retakeAction={() => ({ kind: "saving", failed: true, onRetryUpload })} />);
    expect(screen.getByText("Das neue Foto konnte noch nicht gespeichert werden.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(onRetryUpload).toHaveBeenCalledOnce();

    // The upload's own reason, e.g. an expired session.
    const expired = "Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.";
    rerender(
      <ProcessingJobList items={[failed]} retakeAction={() => ({ kind: "saving", failed: true, error: expired, onRetryUpload })} />,
    );
    expect(screen.getByText(expired)).toBeInTheDocument();
  });

  it("shows the processor's hints under a saved result: amber to check, subtle for information", () => {
    render(
      <ProcessingJobList
        items={[item({ warnings: [UPSCALED, REMOVED] }), item({ photoId: "p2", shotKey: "front", title: "Front", shotOrder: 2 })]}
      />,
    );
    const [checked, plain] = screen.getAllByRole("listitem");
    const removed = screen.getByText(REMOVED.message);
    expect(removed.closest("p")).toHaveClass("text-ae-warning");
    expect(screen.getByText(UPSCALED.message).closest("p")).toHaveClass("text-ae-subtle");
    // The row's status asks for a look (amber), the other one is simply done.
    expect(checked?.lastElementChild).toHaveTextContent("Fertig");
    expect(checked?.lastElementChild).toHaveClass("text-ae-warning");
    expect(plain?.lastElementChild).toHaveTextContent("Fertig");
    expect(plain?.lastElementChild).toHaveClass("text-ae-success");
    expect(plain?.querySelector(".text-ae-warning")).toBeNull();
  });

  it("offers 'Foto neu aufnehmen' for a result whose photo shows the other side of the car", () => {
    render(
      <ProcessingJobList
        items={[item({ warnings: [MIRRORED] })]}
        retakeAction={() => ({ kind: "retake", href: "/fahrzeuge/v1/kamera?shot=front_left_45&zurueck=bearbeiten" })}
      />,
    );
    expect(screen.getByText(MIRRORED.message)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Foto neu aufnehmen: Vorne links (45°)" })).toHaveAttribute(
      "href",
      "/fahrzeuge/v1/kamera?shot=front_left_45&zurueck=bearbeiten",
    );
  });

  it("wraps a long word of a processor text instead of widening the page (e.g. a file name)", () => {
    const token = "Kennzeichenbereich_unsicher_bitte_pruefen_Kennzeichenbereich_unsicher_bitte_pruefen";
    const future = { code: "plate_area_uncertain", message: `Neuer Hinweis: ${token} – bitte das Ergebnis prüfen.` };
    render(
      <ProcessingJobList
        items={[
          item({ warnings: [future, UPSCALED] }),
          item({ photoId: "p2", shotKey: "rear", title: "Hinten", shotOrder: 7, status: "failed", error: `Fehler: ${token}` }),
        ]}
      />,
    );
    // jsdom has no layout: the wrapping rule (measured in Chromium at 390 px) is what keeps it inside.
    for (const text of [future.message, UPSCALED.message, `Fehler: ${token}`]) {
      expect(screen.getByText(text)).toHaveClass("[overflow-wrap:anywhere]");
    }
    expect(screen.getByText(future.message)).toHaveClass("min-w-0");
  });

  it("drops the hints once a new photo of the shot exists", () => {
    render(<ProcessingJobList items={[item({ warnings: [MIRRORED] })]} retakeAction={() => ({ kind: "ready", onProcess: vi.fn() })} />);
    expect(screen.queryByText(MIRRORED.message)).not.toBeInTheDocument();
    expect(screen.getByText("Neues Foto")).toBeInTheDocument();
  });
});
