import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { PhotoRunState } from "@/features/processing/use-processing-run";
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
    ...overrides,
  };
}

const TOO_SMALL = "Fahrzeug im Originalfoto zu klein. Bitte näher fotografieren.";

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

  it("shows a saving new photo and lets a failed upload be retried", () => {
    const onRetryUpload = vi.fn();
    const failed = item({ status: "failed", error: TOO_SMALL, errorCode: "vehicle_too_small" });
    const { rerender } = render(
      <ProcessingJobList items={[failed]} retakeAction={() => ({ kind: "saving", failed: false, onRetryUpload })} />,
    );
    expect(screen.getByText("Neues Foto wird gespeichert…")).toBeInTheDocument();

    rerender(<ProcessingJobList items={[failed]} retakeAction={() => ({ kind: "saving", failed: true, onRetryUpload })} />);
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(onRetryUpload).toHaveBeenCalledOnce();
  });
});
