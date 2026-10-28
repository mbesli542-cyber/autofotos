/**
 * "Fahrzeugdaten" with the real app services (demo backend in memory, real
 * upload queue).
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Activity, type ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import type { VehicleInput } from "@/lib/domain/types";
import { EditVehicleScreen } from "./EditVehicleScreen";

// bfcacheId changes on push/replace navigations, not on the browser's Back.
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), bfcacheId: "_b_1_" }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
// Thumbnails need a real image decoder (canvas), which jsdom does not have.
vi.mock("@/lib/camera/image-utils", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/camera/image-utils")>()),
  prepareImage: async () => ({ width: 4000, height: 3000, thumbnail: null }),
}));

beforeAll(() => {
  URL.createObjectURL = () => "blob:test";
  URL.revokeObjectURL = () => {};
});

beforeEach(() => {
  router.bfcacheId = "_b_1_";
});

const INPUT: VehicleInput = {
  manufacturer: "BMW",
  model: "320d",
  color: null,
  licensePlate: null,
  vin: null,
  mileage: null,
  firstRegistration: null,
  internalReference: null,
  notes: null,
};

async function newVehicle() {
  return getAppServices().backend.data.createVehicle(INPUT);
}

/** Saved on another device (always a later updatedAt than before). */
async function changedElsewhere(vehicleId: string, changes: Partial<VehicleInput>) {
  await new Promise((resolve) => setTimeout(resolve, 5));
  await getAppServices().backend.data.updateVehicle(vehicleId, { ...INPUT, ...changes });
}

function screenFor(vehicleId: string, mode: "visible" | "hidden" = "visible") {
  return (
    <ToastProvider>
      <Activity mode={mode}>
        <EditVehicleScreen vehicleId={vehicleId} />
      </Activity>
    </ToastProvider>
  );
}

const field = (label: RegExp | string) => screen.getByLabelText(label) as HTMLInputElement | HTMLTextAreaElement;
const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const CHANGED_ELSEWHERE = "Die Fahrzeugdaten wurden inzwischen geändert. Bitte prüfen und erneut speichern.";

function gate() {
  let open: () => void = () => {};
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open };
}

describe("<EditVehicleScreen />", () => {
  it("keeps unsaved input when a background upload changes the vehicle status", async () => {
    const { backend, uploads } = getAppServices();
    const vehicle = await newVehicle();
    // The first photo of the new vehicle is still uploading.
    let finishUpload: () => void = () => {};
    const uploaded = new Promise<void>((resolve) => {
      finishUpload = resolve;
    });
    const savePhoto = backend.data.savePhoto.bind(backend.data);
    const spy = vi.spyOn(backend.data, "savePhoto").mockImplementation(async (input) => {
      await uploaded;
      return savePhoto(input);
    });
    await uploads.enqueue({
      vehicleId: vehicle.id,
      shotKey: "front_left_45",
      shotOrder: 1,
      title: "Vorne links (45°)",
      file: new Blob(["jpeg"], { type: "image/jpeg" }),
      takenAt: new Date().toISOString(),
    });

    const loads = vi.spyOn(backend.data, "resolvePhotoUrls");
    render(screenFor(vehicle.id));
    await screen.findByLabelText("FIN");
    fireEvent.change(field("FIN"), { target: { value: "WBA8E9C50GK123456" } });
    fireEvent.change(field(/Notizen/), { target: { value: "Navi, AHK" } });
    const loadsBefore = loads.mock.calls.length;

    await act(async () => finishUpload());
    // The screen reloads the vehicle: new status ("Aufnahmen offen") and updatedAt.
    await waitFor(() => expect(loads.mock.calls.length).toBeGreaterThan(loadsBefore));
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 20)));
    expect((await backend.data.getVehicle(vehicle.id))?.status).toBe("capturing");

    expect(field("FIN").value).toBe("WBA8E9C50GK123456");
    expect(field(/Notizen/).value).toBe("Navi, AHK");
    spy.mockRestore();
    loads.mockRestore();

    // Only the status changed meanwhile – that is no conflict: the input is saved.
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/fahrzeuge/${vehicle.id}`));
    expect(screen.queryByText(CHANGED_ELSEWHERE)).not.toBeInTheDocument();
    const saved = await backend.data.getVehicle(vehicle.id);
    expect([saved?.vin, saved?.notes, saved?.status]).toEqual(["WBA8E9C50GK123456", "Navi, AHK", "capturing"]);
  });

  it("shows the saved data after a successful save", async () => {
    const { backend } = getAppServices();
    const vehicle = await newVehicle();
    const view = render(screenFor(vehicle.id));
    await screen.findByLabelText("Kennzeichen");
    fireEvent.change(field("Kennzeichen"), { target: { value: "sz-ae 63" } });
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));

    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/fahrzeuge/${vehicle.id}`));
    expect((await backend.data.getVehicle(vehicle.id))?.licensePlate).toBe("SZ-AE 63");
    // The route is hidden after the navigation; the next visit shows the saved (normalised) value.
    view.rerender(screenFor(vehicle.id, "hidden"));
    view.rerender(screenFor(vehicle.id));
    await waitFor(() => expect(field("Kennzeichen").value).toBe("SZ-AE 63"));
  });

  it("shows data changed elsewhere when the page is shown again, so saving never undoes it", async () => {
    const { backend } = getAppServices();
    const vehicle = await newVehicle();
    const view = render(screenFor(vehicle.id));
    await screen.findByLabelText("FIN");
    // The employee leaves without typing; meanwhile a colleague adds notes and the FIN.
    view.rerender(screenFor(vehicle.id, "hidden"));
    await changedElsewhere(vehicle.id, { vin: "WVWZZZ1KZAW000001", notes: "Kratzer hinten links" });
    view.rerender(screenFor(vehicle.id));
    await waitFor(() => expect(field(/Notizen/).value).toBe("Kratzer hinten links"));
    expect(field("FIN").value).toBe("WVWZZZ1KZAW000001");

    fireEvent.change(field("Farbe"), { target: { value: "Blau" } });
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    await waitFor(async () => expect((await backend.data.getVehicle(vehicle.id))?.color).toBe("Blau"));
    const saved = await backend.data.getVehicle(vehicle.id);
    expect([saved?.vin, saved?.notes]).toEqual(["WVWZZZ1KZAW000001", "Kratzer hinten links"]);
  });

  it("keeps unsaved input after the browser's Back, a new visit starts from the saved data", async () => {
    const vehicle = await newVehicle();
    const view = render(screenFor(vehicle.id));
    await screen.findByLabelText("FIN");
    fireEvent.change(field(/Notizen/), { target: { value: "Entwurf" } });

    // A look at another page, then Back: the input is still there.
    view.rerender(screenFor(vehicle.id, "hidden"));
    view.rerender(screenFor(vehicle.id));
    await wait(20);
    expect(field(/Notizen/).value).toBe("Entwurf");

    // Left without saving; later "Fahrzeugdaten" again (a new navigation): the saved data.
    view.rerender(screenFor(vehicle.id, "hidden"));
    await changedElsewhere(vehicle.id, { notes: "Gespeichert" });
    router.bfcacheId = "_b_2_";
    view.rerender(screenFor(vehicle.id));
    await waitFor(() => expect(field(/Notizen/).value).toBe("Gespeichert"));
  });

  it("waits for this visit's own load before the data can be changed", async () => {
    const { backend } = getAppServices();
    const vehicle = await newVehicle();
    const view = render(screenFor(vehicle.id));
    await screen.findByLabelText("FIN");
    view.rerender(screenFor(vehicle.id, "hidden"));
    await changedElsewhere(vehicle.id, { notes: "Kollege: Delle Tür" });

    // "Ändern" again (a new visit) – its load takes a moment (Supabase round trips).
    const load = gate();
    const realGet = backend.data.getVehicle.bind(backend.data);
    const get = vi.spyOn(backend.data, "getVehicle").mockImplementation(async (id) => {
      await load.opened;
      return realGet(id);
    });
    router.bfcacheId = "_b_2_";
    view.rerender(screenFor(vehicle.id));
    // The data of the earlier visit is shown read-only until the new data is there …
    expect(screen.getByText("Wird aktualisiert…")).toBeInTheDocument();
    expect(field("Farbe")).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Änderungen speichern" })).toBeDisabled();
    fireEvent.change(field("Farbe"), { target: { value: "Rot" } });
    expect(field("Farbe").value).toBe("");

    // … so the change is made on the newest data and keeps the colleague's notes.
    await act(async () => load.open());
    await waitFor(() => expect(field(/Notizen/).value).toBe("Kollege: Delle Tür"));
    expect(field("Farbe")).not.toHaveAttribute("readonly");
    expect(screen.queryByText("Wird aktualisiert…")).not.toBeInTheDocument();
    get.mockRestore();
    fireEvent.change(field("Farbe"), { target: { value: "Rot" } });
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/fahrzeuge/${vehicle.id}`));
    const saved = await backend.data.getVehicle(vehicle.id);
    expect([saved?.color, saved?.notes]).toEqual(["Rot", "Kollege: Delle Tür"]);
  });

  it("never saves over data changed elsewhere meanwhile – it shows the new data and asks to check it", async () => {
    const { backend } = getAppServices();
    const vehicle = await newVehicle();
    render(screenFor(vehicle.id));
    await screen.findByLabelText("FIN");
    fireEvent.change(field("Farbe"), { target: { value: "Rot" } });
    // Meanwhile a colleague saves notes on another device.
    await changedElsewhere(vehicle.id, { notes: "Kollege: Delle Tür" });
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));

    expect(await screen.findByText(CHANGED_ELSEWHERE)).toBeInTheDocument();
    await waitFor(() => expect(field(/Notizen/).value).toBe("Kollege: Delle Tür"));
    expect(field("Farbe").value).toBe("");
    expect(router.push).not.toHaveBeenCalledWith(`/fahrzeuge/${vehicle.id}`);
    expect((await backend.data.getVehicle(vehicle.id))?.color).toBeNull();

    // Checked, entered again and saved.
    fireEvent.change(field("Farbe"), { target: { value: "Rot" } });
    fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith(`/fahrzeuge/${vehicle.id}`));
    const saved = await backend.data.getVehicle(vehicle.id);
    expect([saved?.color, saved?.notes]).toEqual(["Rot", "Kollege: Delle Tür"]);
  });
});
