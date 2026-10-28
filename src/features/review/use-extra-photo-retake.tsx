"use client";

import { useRef, type ChangeEvent } from "react";
import { useToast } from "@/components/ui/Toast";
import { getAppServices } from "@/lib/app-services";
import { checkPhotoFile, PHOTO_FILE_ACCEPT } from "@/lib/camera/photo-file";
import type { PhotoSlot } from "@/lib/photos/photo-slots";

/**
 * "Foto wiederholen" for an additional photo (Zusatzfoto). Extras are not
 * part of the guided camera, so the employee picks ONE new file. It is saved
 * under exactly that photo's slot (same shot key, order and title), which
 * replaces that photo – the previous one is archived, never overwritten.
 *
 * Render `input` once; call `retake(slot)` from the click handler.
 */
export function useExtraPhotoRetake(vehicleId: string) {
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const slotRef = useRef<PhotoSlot | null>(null);

  function retake(slot: PhotoSlot) {
    slotRef.current = slot;
    inputRef.current?.click();
  }

  async function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0];
    event.target.value = "";
    const slot = slotRef.current;
    slotRef.current = null;
    if (!picked || !slot) return;
    const checked = checkPhotoFile(picked);
    if (!checked.ok) {
      toast.error(checked.message);
      return;
    }
    try {
      await getAppServices().uploads.enqueue({
        vehicleId,
        shotKey: slot.key,
        shotOrder: slot.order,
        title: slot.title,
        file: checked.file,
        // It replaces the current photo now – so it counts as taken now, even
        // if the file itself is older (src/lib/photos/capture-order.ts).
        takenAt: new Date().toISOString(),
      });
      toast.success("Zusatzfoto wird gespeichert…");
    } catch {
      toast.error("Foto konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.");
    }
  }

  const input = (
    <input
      ref={inputRef}
      type="file"
      accept={PHOTO_FILE_ACCEPT}
      className="hidden"
      onChange={handleFileSelected}
      aria-hidden
      tabIndex={-1}
    />
  );

  return { retake, input };
}
