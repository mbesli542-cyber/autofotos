import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import type { PhotoSlot } from "@/lib/photos/photo-slots";
import type { ShotCheckState } from "@/lib/processing/shot-check";
import { PhotoReviewCard } from "./PhotoReviewCard";

function slot(key: string, order: number, title: string, category: PhotoSlot["category"]): PhotoSlot {
  const photo: VehiclePhotoWithUrls = {
    id: `p_${key}`,
    vehicleId: "veh_1",
    shotKey: key,
    shotOrder: order,
    title,
    originalStoragePath: `veh_1/${key}/p_${key}.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    processingWarnings: [],
    thumbnailStoragePath: null,
    width: 4000,
    height: 3000,
    takenAt: "2026-11-05T10:00:00.000Z",
    createdAt: "2026-11-05T10:00:00.000Z",
    updatedAt: "2026-11-05T10:00:00.000Z",
    urls: { original: `/o/${key}.jpg`, thumbnail: `/t/${key}.jpg`, processed: null },
  };
  return { key, order, title, required: true, category, photo, pending: null };
}

describe("<PhotoReviewCard /> state wording", () => {
  const cases: [PhotoSlot, ShotCheckState, string][] = [
    [slot("front", 2, "Vorne", "exterior"), { kind: "ok", hint: null }, "02 Vorne – Geprüft – passt"],
    [slot("front", 2, "Vorne", "exterior"), { kind: "ok", hint: "Bitte prüfen." }, "02 Vorne – Geprüft – bitte Ergebnis ansehen"],
    [slot("rear", 7, "Hinten", "exterior"), { kind: "retake", reason: "Angeschnitten." }, "07 Hinten – Bitte neu aufnehmen: Angeschnitten."],
    [slot("rear", 7, "Hinten", "exterior"), { kind: "error", reason: "Bearbeitung fehlgeschlagen." }, "07 Hinten – Prüfung nicht möglich: Bearbeitung fehlgeschlagen."],
    [slot("rear", 7, "Hinten", "exterior"), { kind: "checking" }, "07 Hinten – Wird geprüft…"],
    [slot("cockpit", 9, "Cockpit", "interior"), { kind: "saved" }, "09 Cockpit – Gespeichert"],
    [slot("rear", 7, "Hinten", "exterior"), { kind: "none" }, "07 Hinten – aufgenommen"],
  ];

  it.each(cases)("names the state with the wording of every screen (%#)", (entry, check, label) => {
    render(<PhotoReviewCard slot={entry} check={check} onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  });
});
