import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { getAppServices } from "@/lib/app-services";
import { NewVehicleScreen } from "./NewVehicleScreen";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

describe("<NewVehicleScreen />", () => {
  it("opens the camera in place of the form, so 'Zurück' never shows an empty form", async () => {
    render(<NewVehicleScreen />);
    fireEvent.change(screen.getByLabelText(/Hersteller/), { target: { value: "Audi" } });
    fireEvent.change(screen.getByLabelText(/Modell/), { target: { value: "A4 Avant" } });
    fireEvent.click(screen.getByRole("button", { name: "Fahrzeug erstellen & Fotos aufnehmen" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledTimes(1));
    const [href] = router.replace.mock.calls[0] as [string];
    const vehicleId = /^\/fahrzeuge\/([^/]+)\/kamera$/.exec(href)?.[1];
    expect(vehicleId).toBeTruthy();
    expect((await getAppServices().backend.data.getVehicle(vehicleId ?? ""))?.model).toBe("A4 Avant");
    expect(router.push).not.toHaveBeenCalled();
    // The (preserved) form is fresh for the next vehicle.
    await waitFor(() => expect(screen.getByLabelText(/Modell/)).toHaveValue(""));
  });

  it("warns at 'Interne Fahrzeugnummer' when another vehicle has the number (export names would collide)", async () => {
    router.replace.mockClear();
    const { data } = getAppServices().backend;
    await data.createVehicle({
      manufacturer: "Mercedes-Benz",
      model: "S 63 AMG",
      color: null,
      licensePlate: null,
      vin: null,
      mileage: null,
      firstRegistration: null,
      internalReference: "FZ-2041",
      notes: null,
    });
    const before = (await data.listVehicles()).length;
    render(<NewVehicleScreen />);
    fireEvent.change(screen.getByLabelText(/Hersteller/), { target: { value: "BMW" } });
    fireEvent.change(screen.getByLabelText(/Modell/), { target: { value: "320d" } });
    // Another vehicle has FZ-2041.
    fireEvent.change(screen.getByLabelText("Interne Fahrzeugnummer"), { target: { value: "fz 2041" } });
    fireEvent.click(screen.getByRole("button", { name: "Fahrzeug erstellen & Fotos aufnehmen" }));

    expect(await screen.findByText("Diese Fahrzeugnummer ist schon vergeben.")).toBeInTheDocument();
    expect(screen.getByLabelText("Interne Fahrzeugnummer")).toHaveFocus();
    expect(screen.getByLabelText("Interne Fahrzeugnummer")).toHaveValue("fz 2041");
    expect(router.replace).not.toHaveBeenCalled();
    expect((await getAppServices().backend.data.listVehicles()).length).toBe(before);

    fireEvent.change(screen.getByLabelText("Interne Fahrzeugnummer"), { target: { value: "FZ-3100" } });
    expect(screen.queryByText("Diese Fahrzeugnummer ist schon vergeben.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fahrzeug erstellen & Fotos aufnehmen" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledTimes(1));
  });
});
