"use client";

import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/FormField";

/**
 * Demo deployments protected with PROCESSING_ACCESS_CODE: small input for
 * the code (saved on this device by the caller).
 */
export function AccessCodeForm({
  error,
  submitLabel = "Speichern",
  onSubmit,
}: {
  error: string | null;
  submitLabel?: string;
  onSubmit: (code: string) => void;
}) {
  const [value, setValue] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = value.trim();
    if (!code) {
      setLocalError("Bitte geben Sie den Zugangscode ein.");
      return;
    }
    setLocalError(null);
    setValue("");
    onSubmit(code);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-xl border border-ae-border bg-ae-surface p-4"
      aria-labelledby="access-code-title"
      noValidate
    >
      <p id="access-code-title" className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <KeyRound className="size-4 text-ae-blue" aria-hidden />
        Zugangscode erforderlich
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <TextField
          id="processing-access-code"
          label="Zugangscode für die Bildbearbeitung"
          className="flex-1"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          error={localError ?? error ?? undefined}
          hint="Wird nur auf diesem Gerät gespeichert."
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="done"
        />
        <Button type="submit" variant="secondary" className="h-12 sm:mt-[26px]">
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
