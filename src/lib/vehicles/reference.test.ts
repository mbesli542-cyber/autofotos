import { describe, expect, it } from "vitest";
import { DUPLICATE_REFERENCE_MESSAGE, findReferenceConflict, referenceKey } from "./reference";

const vehicles = [
  { id: "a", internalReference: "FZ-2041" },
  { id: "b", internalReference: "Lager 7/B" },
  { id: "c", internalReference: null },
];

describe("referenceKey", () => {
  it("compares references as the export file names would", () => {
    expect(referenceKey("FZ-2041")).toBe("fz-2041");
    expect(referenceKey("  fz 2041 ")).toBe("fz-2041");
    expect(referenceKey("Größe 1")).toBe("groesse-1");
  });

  it("has no key without a usable reference", () => {
    expect(referenceKey(null)).toBeNull();
    expect(referenceKey("")).toBeNull();
    expect(referenceKey("///")).toBeNull();
  });
});

describe("findReferenceConflict", () => {
  it("finds another vehicle with the same number – also spelt differently", () => {
    expect(findReferenceConflict("FZ-2041", vehicles)?.id).toBe("a");
    expect(findReferenceConflict("fz 2041", vehicles)?.id).toBe("a");
    expect(findReferenceConflict("LAGER-7-b", vehicles)?.id).toBe("b");
  });

  it("allows a new number and a vehicle keeping its own number", () => {
    expect(findReferenceConflict("FZ-2042", vehicles)).toBeNull();
    expect(findReferenceConflict("FZ-2041", vehicles, "a")).toBeNull();
  });

  it("never reports a conflict without a reference", () => {
    expect(findReferenceConflict(null, vehicles)).toBeNull();
    expect(findReferenceConflict("", vehicles)).toBeNull();
    expect(findReferenceConflict("///", [{ id: "x", internalReference: "///" }])).toBeNull();
  });

  it("has the German message for the form", () => {
    expect(DUPLICATE_REFERENCE_MESSAGE).toBe("Diese Fahrzeugnummer ist schon vergeben.");
  });
});
