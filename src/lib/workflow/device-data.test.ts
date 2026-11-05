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
  it("stops the run and the automatic checks, drops waiting captures, then deletes the vehicle", async () => {
    const order: string[] = [];
    const data = { deleteVehicle: vi.fn(async () => void order.push("data")) } as unknown as DataProvider;
    const uploads = { removeVehicle: vi.fn(async () => void order.push("uploads")) };
    const runs = { reset: vi.fn(() => void order.push("runs")) };
    const checks = { stopVehicle: vi.fn(() => void order.push("checks")) };
    await deleteVehicleFromDevice({ data, uploads, runs, checks }, "veh-1");
    expect(order).toEqual(["runs", "checks", "uploads", "data"]);
    expect(uploads.removeVehicle).toHaveBeenCalledWith("veh-1");
    expect(checks.stopVehicle).toHaveBeenCalledWith("veh-1");
  });

  it("is refused where the backend keeps vehicles (Supabase: no delete policy)", async () => {
    const data = {} as DataProvider;
    expect(canDeleteVehicles(data)).toBe(false);
    const uploads = { removeVehicle: vi.fn(async () => undefined) };
    const checks = { stopVehicle: vi.fn() };
    await expect(deleteVehicleFromDevice({ data, uploads, runs: { reset: vi.fn() }, checks }, "veh-1")).rejects.toThrow();
    expect(checks.stopVehicle).not.toHaveBeenCalled();
    expect(uploads.removeVehicle).not.toHaveBeenCalled();
  });
});
