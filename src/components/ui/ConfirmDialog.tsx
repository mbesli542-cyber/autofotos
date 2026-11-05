"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { isConfirmationTyped } from "@/lib/workflow/device-data";
import { Button } from "./Button";
import { LoadingButton } from "./LoadingButton";

/**
 * Modal confirmation based on the native <dialog> element (focus trap + Esc).
 * `confirmText`: a word the employee has to type before confirming (e.g.
 * "LÖSCHEN" before everything is deleted).
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Abbrechen",
  tone = "danger",
  busy = false,
  confirmText,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  busy?: boolean;
  confirmText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const inputId = useId();
  const [typed, setTyped] = useState("");
  // Every new question starts with an empty field.
  const [shownOpen, setShownOpen] = useState(open);
  if (shownOpen !== open) {
    setShownOpen(open);
    if (open) setTyped("");
  }
  const confirmed = confirmText === undefined || isConfirmationTyped(typed, confirmText);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // Close when the route gets hidden (Next.js keeps routes mounted): a modal
  // left open in the hidden route would block the page that is shown.
  useLayoutEffect(() => () => ref.current?.close(), []);

  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      className="m-auto w-[min(92vw,420px)] rounded-2xl border border-ae-border bg-ae-surface p-0 text-ae-text shadow-[var(--shadow-card)]"
      aria-labelledby="confirm-title"
    >
      <div className="p-5">
        <h2 id="confirm-title" className="text-lg font-semibold">
          {title}
        </h2>
        {description && <p className="mt-2 text-sm text-ae-muted">{description}</p>}
        {confirmText !== undefined && (
          <div className="mt-4 flex flex-col gap-1.5">
            <label htmlFor={inputId} className="text-sm font-medium text-ae-muted">
              Zum Bestätigen <span className="font-bold text-ae-text">{confirmText}</span> eingeben
            </label>
            <input
              id={inputId}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              autoCapitalize="characters"
              autoCorrect="off"
              spellCheck={false}
              disabled={busy}
              className="h-11 w-full rounded-xl border border-ae-border bg-ae-surface-2 px-3.5 text-base text-ae-text placeholder:text-ae-subtle focus:border-ae-blue focus:ring-2 focus:ring-ae-blue/30 focus:outline-none"
            />
          </div>
        )}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <LoadingButton
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={onConfirm}
            loading={busy}
            disabled={!confirmed}
          >
            {confirmLabel}
          </LoadingButton>
        </div>
      </div>
    </dialog>
  );
}
