/**
 * Tabs "Kamera" and "Bearbeiten" with the real app services (demo backend in
 * memory, including the demo vehicles).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { getAppServices } from "@/lib/app-services";
import { completeCapture, savePhotoAndSyncStatus } from "@/lib/workflow/vehicle-workflow";
import { VehicleHubScreen } from "./VehicleHubScreen";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

beforeAll(() => {
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => {};
});

/** A vehicle with every required photo; "Aufnahmen abschließen" optional. */
async function fullyCapturedVehicle(model: string, { completed }: { completed: boolean }) {
  const { backend, template } = getAppServices();
  const data = backend.data;
  const vehicle = await data.createVehicle({
    manufacturer: "Skoda",
    model,
    color: null,
    licensePlate: null,
    vin: null,
    mileage: null,
    firstRegistration: null,
    internalReference: null,
    notes: null,
  });
  for (const shot of template.shots) {
    await savePhotoAndSyncStatus(data, template, {
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
  if (completed) await completeCapture(data, template, vehicle.id);
  return vehicle;
}

describe("<VehicleHubScreen hub='processing' />", () => {
  it("marks a vehicle with a rejected shot 'Neu aufnehmen' – the others not", async () => {
    const rejected = await fullyCapturedVehicle("Fabia", { completed: true });
    const fine = await fullyCapturedVehicle("Scala", { completed: true });
    const { data } = getAppServices().backend;
    const front = (await data.listPhotos(rejected.id)).find((photo) => photo.shotKey === "front");
    if (!front) throw new Error("photo missing");
    await data.saveProcessedPhoto({
      photo: front,
      preset: "autoexperten_standard",
      file: new Blob(["result"], { type: "image/jpeg" }),
      warnings: [{ code: "plate_mirrored", message: "Das Foto zeigt die andere Fahrzeugseite." }],
    });
    render(<VehicleHubScreen hub="processing" />);

    const card = (vehicleId: string) => {
      const link = screen
        .getAllByRole("link")
        .find((entry) => entry.getAttribute("href") === `/fahrzeuge/${vehicleId}/bearbeiten`);
      if (!link) throw new Error("card missing");
      return link;
    };
    await screen.findAllByRole("link");
    await waitFor(() => expect(within(card(rejected.id)).getByText("Neu aufnehmen")).toBeInTheDocument());
    expect(within(card(fine.id)).queryByText("Neu aufnehmen")).toBeNull();
  });

  it("lists fully captured vehicles that still need 'Aufnahmen abschließen'", async () => {
    const open = await fullyCapturedVehicle("Octavia", { completed: false });
    const done = await fullyCapturedVehicle("Superb", { completed: true });
    render(<VehicleHubScreen hub="processing" />);

    const step = await screen.findByRole("region", { name: "Bereit zum Abschließen" });
    const action = within(step).getByRole("link", { name: "Aufnahmen abschließen: Skoda Octavia" });
    expect(action).toHaveAttribute("href", `/fahrzeuge/${open.id}/fotos`);
    expect(action).toHaveTextContent("Aufnahmen abschließen");
    expect(within(step).queryByText("Skoda Superb")).toBeNull();

    // The vehicles ready for processing are listed as before, outside the new section.
    const ready = screen
      .getAllByRole("link")
      .find((link) => link.getAttribute("href") === `/fahrzeuge/${done.id}/bearbeiten`);
    expect(ready).toBeDefined();
    expect(step.contains(ready ?? null)).toBe(false);
    expect(screen.queryAllByRole("link").some((link) => link.getAttribute("href") === `/fahrzeuge/${open.id}/bearbeiten`)).toBe(false);
  });

  it("keeps the camera tab for vehicles with open or rejected shots only", async () => {
    const open = await fullyCapturedVehicle("Kodiaq", { completed: false });
    render(<VehicleHubScreen hub="camera" />);
    await screen.findByRole("heading", { level: 2, name: "Aufnahmen" });
    await screen.findAllByRole("link");
    expect(screen.queryByRole("region", { name: "Bereit zum Abschließen" })).toBeNull();
    expect(screen.queryAllByRole("link").some((link) => link.getAttribute("href")?.startsWith(`/fahrzeuge/${open.id}`))).toBe(false);
  });
});

describe("<VehicleHubScreen hub='camera' />", () => {
  it("lists a fully captured vehicle with a rejected shot and opens the camera at that shot", async () => {
    const rejected = await fullyCapturedVehicle("Enyaq", { completed: true });
    const fine = await fullyCapturedVehicle("Karoq", { completed: true });
    const { data } = getAppServices().backend;
    const front = (await data.listPhotos(rejected.id)).find((photo) => photo.shotKey === "front");
    if (!front) throw new Error("photo missing");
    await data.saveProcessedPhoto({
      photo: front,
      preset: "autoexperten_standard",
      file: new Blob(["result"], { type: "image/jpeg" }),
      warnings: [{ code: "plate_mirrored", message: "Das Foto zeigt die andere Fahrzeugseite." }],
    });
    render(<VehicleHubScreen hub="camera" />);

    const link = await waitFor(() => {
      const found = screen
        .getAllByRole("link")
        .find((entry) => entry.getAttribute("href") === `/fahrzeuge/${rejected.id}/kamera?shot=front`);
      if (!found) throw new Error("card missing");
      return found;
    });
    expect(within(link).getByText("Neu aufnehmen")).toBeInTheDocument();
    expect(within(link).queryByText("Vollständig")).toBeNull();
    // A complete set without a red shot is done – and the tab never claims "all complete" while one is red.
    expect(screen.queryAllByRole("link").some((entry) => entry.getAttribute("href")?.startsWith(`/fahrzeuge/${fine.id}`))).toBe(false);
    expect(screen.queryByText("Keine offenen Aufnahmen")).toBeNull();
  });
});
