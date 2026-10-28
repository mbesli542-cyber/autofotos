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
});
