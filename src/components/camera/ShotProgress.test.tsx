import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { VehiclePhotoWithUrls } from "@/lib/domain/types";
import { buildPhotoSlots } from "@/lib/photos/photo-slots";
import { AUTOEXPERTEN_STANDARD_TEMPLATE as T } from "@/lib/shots/shot-template";
import { ShotProgress } from "./ShotProgress";

function photo(shotKey: string, shotOrder: number): VehiclePhotoWithUrls {
  return {
    id: `id-${shotKey}`,
    vehicleId: "v1",
    shotKey,
    shotOrder,
    title: shotKey,
    originalStoragePath: `v1/${shotKey}/x.jpg`,
    processedStoragePath: null,
    processedPreset: null,
    thumbnailStoragePath: null,
    width: null,
    height: null,
    takenAt: "2026-10-07T10:00:00Z",
    createdAt: "2026-10-07T10:00:00Z",
    updatedAt: "2026-10-07T10:00:00Z",
    urls: { original: "/x.jpg", thumbnail: "/x.jpg", processed: null },
  };
}

describe("<ShotProgress />", () => {
  const slots = buildPhotoSlots(T, [photo("front_left_45", 1), photo("front", 2)]).required;

  it("renders one step per required shot and marks captured shots", () => {
    render(<ShotProgress slots={slots} currentKey="front_right_45" onSelect={() => {}} />);
    const steps = screen.getAllByRole("button");
    expect(steps).toHaveLength(15);
    expect(screen.getByRole("button", { name: "1. Vorne links (45°) – aufgenommen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3. Vorne rechts (45°) – offen" })).toHaveAttribute(
      "aria-current",
      "step",
    );
  });

  it("lets the employee jump to a shot", () => {
    const onSelect = vi.fn();
    render(<ShotProgress slots={slots} currentKey="front_right_45" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /^7\. Hinten/ }));
    expect(onSelect).toHaveBeenCalledWith("rear");
  });
});
