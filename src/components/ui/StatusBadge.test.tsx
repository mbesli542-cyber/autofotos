import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("<StatusBadge />", () => {
  it("uses the status tint on the app background", () => {
    render(<StatusBadge status="processed" />);
    const badge = screen.getByText("Bearbeitet");
    expect(badge).toHaveClass("bg-ae-blue/15", "text-[#5aa6ff]");
    expect(badge).not.toHaveClass("bg-ae-bg");
  });

  it("gets an opaque dark background on top of a photo (no translucent tint left to win)", () => {
    for (const [status, label, tint, text] of [
      ["processed", "Bearbeitet", "bg-ae-blue/15", "text-[#5aa6ff]"],
      ["complete", "Vollständig", "bg-ae-success/12", "text-ae-success"],
      ["capturing", "Aufnahmen offen", "bg-ae-warning/12", "text-ae-warning"],
      ["new", "Neu", "bg-ae-surface-3", "text-ae-muted"],
    ] as const) {
      render(<StatusBadge status={status} onImage className="absolute top-3 left-3" />);
      const badge = screen.getByText(label);
      expect(badge).toHaveClass("bg-ae-bg", text, "absolute");
      expect(badge).not.toHaveClass(tint);
      expect([...badge.classList].filter((name) => name.startsWith("bg-"))).toEqual(["bg-ae-bg"]);
    }
  });
});
