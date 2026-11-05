import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { photoChecks, type AutoCheckAvailability } from "@/lib/processing/photo-checks";
import { useVehicleChecks } from "./use-photo-checks";

afterEach(() => {
  vi.restoreAllMocks();
});

function spies() {
  const unwatch = vi.fn();
  return {
    checkVehicle: vi.spyOn(photoChecks, "checkVehicle").mockResolvedValue(),
    watch: vi.spyOn(photoChecks, "watch").mockReturnValue(unwatch),
    unwatch,
  };
}

describe("useVehicleChecks", () => {
  it("checks the vehicle's photos when the page is shown with the checks on", () => {
    const { checkVehicle } = spies();
    renderHook(() => useVehicleChecks("veh_1", "on"));
    expect(checkVehicle).toHaveBeenCalledTimes(1);
    expect(checkVehicle).toHaveBeenCalledWith("veh_1");
  });

  it("checks them as soon as the checks come on – also from 'not asked yet' after a reload", () => {
    const { checkVehicle } = spies();
    const { rerender } = renderHook(({ availability }) => useVehicleChecks("veh_1", availability), {
      initialProps: { availability: "unknown" as AutoCheckAvailability },
    });
    expect(checkVehicle).not.toHaveBeenCalled();
    rerender({ availability: "on" });
    expect(checkVehicle).toHaveBeenCalledTimes(1);
    // Staying on checks nothing again; off and on again (connection back) does.
    rerender({ availability: "on" });
    rerender({ availability: "offline" });
    expect(checkVehicle).toHaveBeenCalledTimes(1);
    rerender({ availability: "on" });
    expect(checkVehicle).toHaveBeenCalledTimes(2);
  });

  it("watches the processor's availability only while the page is shown", () => {
    const { watch, unwatch } = spies();
    const { unmount } = renderHook(() => useVehicleChecks("veh_1", "unreachable"));
    expect(watch).toHaveBeenCalledTimes(1);
    expect(unwatch).not.toHaveBeenCalled();
    unmount();
    expect(unwatch).toHaveBeenCalledTimes(1);
  });
});
