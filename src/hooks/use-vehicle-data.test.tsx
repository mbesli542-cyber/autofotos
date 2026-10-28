/**
 * useVehicleDetail with the real app services (demo mode): loading a vehicle
 * reconciles the local upload queue with the photos saved meanwhile.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/camera/image-utils", () => ({
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));

import { getAppServices } from "@/lib/app-services";
import { AppError } from "@/lib/errors";
import { createId } from "@/lib/ids";
import { useVehicleDetail } from "./use-vehicle-data";

function VehicleDetail({ vehicleId }: { vehicleId: string }) {
  const { state } = useVehicleDetail(vehicleId);
  return <p>{state.status === "ready" ? `${state.photos.length} Foto(s)` : state.status}</p>;
}

/** A capture of "Vorne" that the server refused for good (unsupported type). */
async function vehicleWithRefusedCapture(takenAt: string) {
  const { backend, uploads } = getAppServices();
  const vehicle = await backend.data.createVehicle({
    manufacturer: "Seat",
    model: "Leon",
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  vi.spyOn(backend.data, "savePhoto").mockRejectedValueOnce(new AppError("unsupported_file"));
  const refused = await uploads.enqueue({
    vehicleId: vehicle.id,
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    file: new Blob(["avif"], { type: "image/avif" }),
    takenAt,
  });
  await waitFor(() =>
    expect(uploads.getSnapshot().items.find((item) => item.id === refused.id)).toMatchObject({
      status: "failed",
      retryable: false,
    }),
  );
  return { vehicleId: vehicle.id, refused };
}

/** A photo of "Vorne" saved on another device – this queue never saw it. */
async function savedOnAnotherDevice(vehicleId: string, takenAt: string) {
  await getAppServices().backend.data.savePhoto({
    photoId: createId(),
    vehicleId,
    shotKey: "front",
    shotOrder: 2,
    title: "Vorne",
    file: new Blob(["jpeg"], { type: "image/jpeg" }),
    thumbnail: null,
    width: 4000,
    height: 3000,
    takenAt,
  });
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(async () => {
  vi.restoreAllMocks();
  await getAppServices().uploads.clear();
});

const queuedIds = () => getAppServices().uploads.getSnapshot().items.map((item) => item.id);

describe("useVehicleDetail – refused captures behind a newer saved photo", () => {
  it("drops a refused capture that is older than the saved photo of its shot", async () => {
    const { vehicleId, refused } = await vehicleWithRefusedCapture("2026-10-28T10:00:00.000Z");
    await savedOnAnotherDevice(vehicleId, "2026-10-28T11:00:00.000Z");

    render(<VehicleDetail vehicleId={vehicleId} />);
    await screen.findByText("1 Foto(s)");
    // Hidden behind that photo it could neither be saved nor discarded – and
    // "N nicht gespeichert" would count it forever.
    await waitFor(() => expect(queuedIds()).not.toContain(refused.id));
  });

  it("keeps a refused capture that is newer than the saved photo (shown in its slot, can be discarded)", async () => {
    const { vehicleId, refused } = await vehicleWithRefusedCapture("2026-10-28T12:00:00.000Z");
    await savedOnAnotherDevice(vehicleId, "2026-10-28T11:00:00.000Z");

    render(<VehicleDetail vehicleId={vehicleId} />);
    await screen.findByText("1 Foto(s)");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(queuedIds()).toContain(refused.id);
  });
});
