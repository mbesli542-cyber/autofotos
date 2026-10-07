"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { TextAreaField, TextField } from "@/components/ui/FormField";
import { LoadingButton } from "@/components/ui/LoadingButton";
import { StickyActions } from "@/components/layout/PageContainer";
import type { VehicleInput } from "@/lib/domain/types";
import {
  emptyVehicleFormValues,
  validateVehicleForm,
  VEHICLE_FIELD_LIMITS,
  type VehicleFormErrors,
  type VehicleFormField,
  type VehicleFormValues,
} from "@/lib/vehicles/validation";

const MANUFACTURER_SUGGESTIONS = [
  "Audi",
  "BMW",
  "Ford",
  "Mercedes-Benz",
  "Mercedes-AMG",
  "Mini",
  "Opel",
  "Porsche",
  "Seat",
  "Cupra",
  "Škoda",
  "Tesla",
  "Toyota",
  "Volkswagen",
  "Volvo",
];

/**
 * Vehicle data form (create + edit). Only Hersteller and Modell are required.
 */
export function VehicleForm({
  initialValues,
  submitLabel,
  loadingText,
  submitIcon,
  onSubmit,
}: {
  initialValues?: VehicleFormValues;
  submitLabel: string;
  loadingText: string;
  submitIcon?: ReactNode;
  /** Should throw (with a German message) on failure. */
  onSubmit: (input: VehicleInput) => Promise<void>;
}) {
  const [values, setValues] = useState<VehicleFormValues>(initialValues ?? emptyVehicleFormValues());
  const [errors, setErrors] = useState<VehicleFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function field(name: VehicleFormField) {
    return {
      id: name,
      value: values[name],
      error: errors[name],
      onChange: (event: { target: { value: string } }) => {
        const value = event.target.value;
        setValues((current) => ({ ...current, [name]: value }));
        if (errors[name]) setErrors((current) => ({ ...current, [name]: undefined }));
      },
    };
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    const result = validateVehicleForm(values);
    if (!result.ok) {
      setErrors(result.errors);
      const firstInvalid = Object.keys(result.errors)[0];
      if (firstInvalid) document.getElementById(firstInvalid)?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(result.value);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Bitte versuchen Sie es erneut.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-6">
      <fieldset className="rounded-2xl border border-ae-border bg-ae-surface p-4 sm:p-5">
        <legend className="px-1 text-sm font-semibold text-ae-text">Fahrzeugdaten</legend>
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          <TextField
            {...field("manufacturer")}
            label="Hersteller"
            required
            autoComplete="off"
            list="manufacturer-suggestions"
            maxLength={VEHICLE_FIELD_LIMITS.manufacturer}
            placeholder="z. B. Mercedes-Benz"
          />
          <datalist id="manufacturer-suggestions">
            {MANUFACTURER_SUGGESTIONS.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <TextField
            {...field("model")}
            label="Modell"
            required
            autoComplete="off"
            maxLength={VEHICLE_FIELD_LIMITS.model}
            placeholder="z. B. S 63 AMG"
          />
          <TextField
            {...field("color")}
            label="Farbe"
            autoComplete="off"
            maxLength={VEHICLE_FIELD_LIMITS.color}
            placeholder="z. B. Obsidianschwarz metallic"
          />
          <TextField
            {...field("licensePlate")}
            label="Kennzeichen"
            autoComplete="off"
            autoCapitalize="characters"
            maxLength={VEHICLE_FIELD_LIMITS.licensePlate}
            placeholder="z. B. SZ-AE 123"
          />
          <TextField
            {...field("vin")}
            label="FIN"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={20}
            placeholder="17-stellige Fahrzeug-Identnummer"
            className="sm:col-span-2"
          />
        </div>
      </fieldset>

      <fieldset className="rounded-2xl border border-ae-border bg-ae-surface p-4 sm:p-5">
        <legend className="px-1 text-sm font-semibold text-ae-text">Weitere Angaben</legend>
        <div className="mt-2 grid gap-4 sm:grid-cols-2">
          <TextField
            {...field("mileage")}
            label="Kilometerstand"
            inputMode="numeric"
            autoComplete="off"
            placeholder="z. B. 48.500"
          />
          <TextField {...field("firstRegistration")} label="Erstzulassung" type="date" />
          <TextField
            {...field("internalReference")}
            label="Interne Fahrzeugnummer"
            autoComplete="off"
            maxLength={VEHICLE_FIELD_LIMITS.internalReference}
            placeholder="z. B. FZ-2041"
            hint="Wird für die Dateinamen beim Export verwendet."
          />
          <TextAreaField
            {...field("notes")}
            label="Notizen"
            maxLength={VEHICLE_FIELD_LIMITS.notes}
            placeholder="Ausstattung, Besonderheiten, Schäden …"
            className="sm:col-span-2"
          />
        </div>
      </fieldset>

      <p className="-mt-2 text-xs text-ae-subtle">
        <span className="text-ae-blue">*</span> Pflichtfelder
      </p>

      <StickyActions className="mt-0">
        {submitError && (
          <p role="alert" className="mb-3 text-sm text-ae-danger">
            {submitError}
          </p>
        )}
        <LoadingButton
          type="submit"
          size="lg"
          fullWidth
          loading={submitting}
          loadingText={loadingText}
          icon={submitIcon}
          className="sm:w-auto sm:min-w-80"
        >
          {submitLabel}
        </LoadingButton>
      </StickyActions>
    </form>
  );
}
