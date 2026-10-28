import { fireEvent, render, screen } from "@testing-library/react";
import { Activity } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";
import { installDialogPolyfill } from "./dialog-test-polyfill";

beforeAll(() => {
  installDialogPolyfill();
});

function ui(mode: "visible" | "hidden", onConfirm = vi.fn(), onCancel = vi.fn()) {
  return (
    <Activity mode={mode}>
      <ConfirmDialog open title="Demo-Daten zurücksetzen?" confirmLabel="Zurücksetzen" onConfirm={onConfirm} onCancel={onCancel} />
    </Activity>
  );
}

describe("<ConfirmDialog />", () => {
  it("opens as a modal and reports the choice", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(ui("visible", onConfirm, onCancel));
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "Zurücksetzen" }));
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("closes itself when its route is hidden (Next.js keeps routes mounted)", () => {
    const view = render(ui("visible"));
    const dialog = document.querySelector("dialog");
    expect(dialog?.open).toBe(true);
    // A modal left open in the hidden route would block the page that is shown.
    view.rerender(ui("hidden"));
    expect(dialog?.open).toBe(false);
  });
});
