import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VehicleForm } from "./VehicleForm";

describe("<VehicleForm />", () => {
  it("shows German validation errors and does not submit without required fields", async () => {
    const onSubmit = vi.fn();
    render(<VehicleForm submitLabel="Fahrzeug erstellen & Fotos aufnehmen" loadingText="Fahrzeug wird erstellt…" onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("button", { name: "Fahrzeug erstellen & Fotos aufnehmen" }));

    expect(await screen.findByText("Bitte geben Sie den Hersteller an.")).toBeInTheDocument();
    expect(screen.getByText("Bitte geben Sie das Modell an.")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits normalised data", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<VehicleForm submitLabel="Speichern" loadingText="…" onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText(/Hersteller/), { target: { value: "Mercedes-Benz" } });
    fireEvent.change(screen.getByLabelText(/Modell/), { target: { value: "S 63 AMG" } });
    fireEvent.change(screen.getByLabelText("Kennzeichen"), { target: { value: "sz-ae 63" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      manufacturer: "Mercedes-Benz",
      model: "S 63 AMG",
      licensePlate: "SZ-AE 63",
    });
  });
});
