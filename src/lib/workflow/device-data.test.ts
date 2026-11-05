import { describe, expect, it, vi } from "vitest";
import type { DataProvider } from "@/lib/data/types";
import { canDeleteVehicles, deleteVehicleFromDevice, isConfirmationTyped } from "./device-data";

describe("isConfirmationTyped", () => {
  it("accepts LÖSCHEN in any case with surrounding spaces only", () => {
    expect(isConfirmationTyped("LÖSCHEN")).toBe(true);
    expect(isConfirmationTyped("  löschen ")).toBe(true);
    expect(isConfirmationTyped("LOESCHEN")).toBe(false);
    expect(isConfirmationTyped("LÖSCH")).toBe(false);
    expect(isConfirmationTyped("")).toBe(false);
  });
});

describe("deleteVehicleFromDevice", () => {
  it("stops the run, drops waiting captures, then deletes the vehicle", async () => {
    const order: string[] = [];
    const data = { deleteVehicle: vi.fn(async () => void order.push("data")) } as unknown as DataProvider;
    const uploads = { removeVehicle: vi.fn(async () => void order.push("uploads")) };
    const runs = { reset: vi.fn(() => void order.push("runs")) };
    await deleteVehicleFromDevice({ data, uploads, runs }, "veh-1");
    expect(order).toEqual(["runs", "uploads", "data"]);
    expect(uploads.removeVehicle).toHaveBeenCalledWith("veh-1");
  });

  it("is refused where the backend keeps vehicles (Supabase: no delete policy)", async () => {
    const data = {} as DataProvider;
    expect(canDeleteVehicles(data)).toBe(false);
    const uploads = { removeVehicle: vi.fn(async () => undefined) };
    await expect(deleteVehicleFromDevice({ data, uploads, runs: { reset: vi.fn() } }, "veh-1")).rejects.toThrow();
    expect(uploads.removeVehicle).not.toHaveBeenCalled();
  });
});
