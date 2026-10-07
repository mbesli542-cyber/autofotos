import { describe, expect, it } from "vitest";
import {
  emptyVehicleFormValues,
  parseGermanOrIsoDate,
  parseMileage,
  validateVehicleForm,
  type VehicleFormValues,
} from "./validation";

const TODAY = new Date("2026-10-07T10:00:00Z");

function form(values: Partial<VehicleFormValues>): VehicleFormValues {
  return { ...emptyVehicleFormValues(), ...values };
}

describe("validateVehicleForm", () => {
  it("requires only manufacturer and model", () => {
    const result = validateVehicleForm(form({}), { today: TODAY });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(Object.keys(result.errors).sort()).toEqual(["manufacturer", "model"]);
      expect(result.errors.manufacturer).toBe("Bitte geben Sie den Hersteller an.");
    }

    const minimal = validateVehicleForm(form({ manufacturer: "Mercedes-Benz", model: "S 63 AMG" }), { today: TODAY });
    expect(minimal).toEqual({
      ok: true,
      value: {
        manufacturer: "Mercedes-Benz",
        model: "S 63 AMG",
        color: null,
        licensePlate: null,
        vin: null,
        mileage: null,
        firstRegistration: null,
        internalReference: null,
        notes: null,
      },
    });
  });

  it("treats whitespace-only required fields as empty", () => {
    const result = validateVehicleForm(form({ manufacturer: "   ", model: "X5" }), { today: TODAY });
    expect(result.ok).toBe(false);
  });

  it("normalises all optional fields", () => {
    const result = validateVehicleForm(
      form({
        manufacturer: "  BMW ",
        model: "X5   xDrive30d",
        color: " Mineralweiß ",
        licensePlate: "hd-bx  530",
        vin: "wbacv610x0lh12345",
        mileage: "72.300 km",
        firstRegistration: "02.06.2020",
        internalReference: " FZ-2042 ",
        notes: "  Anhängerkupplung  ",
      }),
      { today: TODAY },
    );
    expect(result).toEqual({
      ok: true,
      value: {
        manufacturer: "BMW",
        model: "X5 xDrive30d",
        color: "Mineralweiß",
        licensePlate: "HD-BX 530",
        vin: "WBACV610X0LH12345",
        mileage: 72300,
        firstRegistration: "2020-06-02",
        internalReference: "FZ-2042",
        notes: "Anhängerkupplung",
      },
    });
  });

  it("rejects invalid VINs (length, I/O/Q)", () => {
    for (const vin of ["WDD222186", "WDD2221861A12345I", "WDD2221861A12345O"]) {
      const result = validateVehicleForm(form({ manufacturer: "A", model: "B", vin }), { today: TODAY });
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.errors.vin).toMatch(/17 Zeichen/);
    }
  });

  it("rejects invalid mileage, plates and dates", () => {
    const result = validateVehicleForm(
      form({
        manufacturer: "Audi",
        model: "A6",
        mileage: "ca. 40tkm",
        licensePlate: "MA_AU#1",
        firstRegistration: "2027-01-01",
      }),
      { today: TODAY },
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.mileage).toBeDefined();
      expect(result.errors.licensePlate).toBeDefined();
      expect(result.errors.firstRegistration).toBe("Die Erstzulassung darf nicht in der Zukunft liegen.");
    }
  });
});

describe("field parsers", () => {
  it("parses mileage in German notation", () => {
    expect(parseMileage("")).toBeNull();
    expect(parseMileage("48500")).toBe(48500);
    expect(parseMileage("48.500")).toBe(48500);
    expect(parseMileage("48 500 km")).toBe(48500);
    expect(parseMileage("-5")).toBe("invalid");
    expect(parseMileage("3000000")).toBe("invalid");
  });

  it("parses ISO and German dates and rejects impossible ones", () => {
    expect(parseGermanOrIsoDate("2021-03-15")).toBe("2021-03-15");
    expect(parseGermanOrIsoDate("5.1.2022")).toBe("2022-01-05");
    expect(parseGermanOrIsoDate("31.02.2022")).toBe("invalid");
    expect(parseGermanOrIsoDate("März 2021")).toBe("invalid");
  });
});
