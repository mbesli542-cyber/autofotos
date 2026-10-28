import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PROCESSING_PRESETS } from "@/lib/processing/presets";
import { ProcessingPresetCard } from "./ProcessingPresetCard";

describe("<ProcessingPresetCard />", () => {
  it("cannot select a style the processor does not implement yet", () => {
    const onSelect = vi.fn();
    render(<ProcessingPresetCard preset={PROCESSING_PRESETS.autoexperten_dark} selected={false} onSelect={onSelect} />);
    const card = screen.getByRole("radio", { name: /AutoExperten Dark/ });
    expect(card).toBeDisabled();
    expect(card).toHaveTextContent("Demnächst verfügbar");
    fireEvent.click(card);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("selects an available style", () => {
    const onSelect = vi.fn();
    render(<ProcessingPresetCard preset={PROCESSING_PRESETS.autoexperten_standard} selected onSelect={onSelect} />);
    const card = screen.getByRole("radio", { name: /AutoExperten Standard/ });
    expect(card).toBeEnabled();
    expect(card).toHaveAttribute("aria-checked", "true");
    expect(card).not.toHaveTextContent("Demnächst verfügbar");
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledOnce();
  });
});
