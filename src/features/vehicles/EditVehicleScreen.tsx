"use client";

import { Save, TriangleAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useLayoutEffect, useState } from "react";
import { AppHeader } from "@/components/layout/AppHeader";
import { PageContainer } from "@/components/layout/PageContainer";
import { ErrorState } from "@/components/ui/ErrorState";
import { PageLoading, Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { VehicleForm } from "@/components/vehicles/VehicleForm";
import { useVehicleDetail } from "@/hooks/use-vehicle-data";
import { getAppServices } from "@/lib/app-services";
import type { Vehicle, VehicleInput } from "@/lib/domain/types";
import { toUserMessage, VehicleChangedError } from "@/lib/errors";
import { vehicleToFormValues } from "@/lib/vehicles/validation";
import { saveVehicleData } from "@/lib/workflow/vehicle-workflow";
import { VehicleNotFound } from "./VehicleNotFound";

/** The saved vehicle while it is newer than the loaded one, else the loaded one. */
function newestVehicle(loaded: Vehicle, saved: Vehicle | null): Vehicle {
  return saved && Date.parse(saved.updatedAt) > Date.parse(loaded.updatedAt) ? saved : loaded;
}

/** The form holds unsaved input of this visit, entered on the data of `base`. */
interface EditedForm {
  visit: string;
  formKey: string;
  base: Vehicle;
}

export function EditVehicleScreen({ vehicleId }: { vehicleId: string }) {
  const router = useRouter();
  const toast = useToast();
  const { state, reload } = useVehicleDetail(vehicleId);
  /** Saved here (or found changed by the save) – newer than the loaded data until the next load. */
  const [saved, setSaved] = useState<Vehicle | null>(null);
  // The form shows the newest data until the employee changes something. From
  // then on its key is fixed: background reloads (e.g. an upload that changed
  // the status) never discard the input. Routes stay mounted: the browser's
  // Back keeps the input, a new visit (bfcacheId changes on push/replace
  // navigations) starts from the newest data again.
  const [edited, setEdited] = useState<EditedForm | null>(null);
  /** The save found the data changed elsewhere: the form now shows the new data. */
  const [changedElsewhere, setChangedElsewhere] = useState<string | null>(null);
  const detailHref = `/fahrzeuge/${vehicleId}`;

  // A note of an earlier visit does not apply to the next one.
  useLayoutEffect(() => () => setChangedElsewhere(null), []);

  const vehicle = state.status === "ready" ? newestVehicle(state.vehicle, saved) : null;
  const editedHere = edited && edited.visit === router.bfcacheId ? edited : null;
  // Until this visit's own load returned, the page shows the data of an
  // earlier visit: the form waits for the newest data (read-only) instead
  // of taking the old data as the base of a change.
  const updating = state.status === "ready" && state.stale === true && !editedHere;
  const dataKey = vehicle ? `${router.bfcacheId}-${vehicle.id}-${vehicle.updatedAt}` : "";
  const formKey = editedHere ? editedHere.formKey : dataKey;

  async function handleSave(input: VehicleInput) {
    const base = editedHere?.base ?? vehicle;
    if (!base) return;
    let updated: Vehicle;
    try {
      // Never over a change made elsewhere since the form got its data.
      updated = await saveVehicleData(getAppServices().backend.data, vehicleId, input, base);
    } catch (error) {
      if (error instanceof VehicleChangedError) {
        // Show the changed data (fresh form) and ask to check it and save again.
        if (error.current) setSaved(error.current);
        setEdited(null);
        setChangedElsewhere(toUserMessage(error));
        reload();
        return;
      }
      throw new Error(toUserMessage(error, "Fahrzeugdaten konnten nicht gespeichert werden."));
    }
    // Fresh form with the saved (normalised) data.
    setSaved(updated);
    setEdited(null);
    setChangedElsewhere(null);
    toast.success("Fahrzeugdaten gespeichert.");
    router.push(detailHref);
  }

  function handleEdit() {
    if (!vehicle) return;
    setEdited((current) =>
      current && current.visit === router.bfcacheId ? current : { visit: router.bfcacheId, formKey, base: vehicle },
    );
  }

  return (
    <>
      <AppHeader title="Fahrzeugdaten" backHref={detailHref} />
      <PageContainer className="max-w-2xl">
        {state.status === "loading" && <PageLoading message="Fahrzeug wird geladen…" />}
        {state.status === "error" && <ErrorState message={state.message} onRetry={reload} />}
        {state.status === "not_found" && <VehicleNotFound />}
        {vehicle && (
          <>
            {changedElsewhere && !updating && (
              <p
                role="alert"
                className="mb-4 flex items-start gap-2 rounded-xl border border-ae-warning/30 bg-ae-warning/8 px-4 py-3 text-sm text-ae-text"
              >
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-ae-warning" aria-hidden />
                {changedElsewhere}
              </p>
            )}
            {updating && (
              <p className="mb-4 flex items-center gap-2 text-sm text-ae-muted" role="status">
                <Spinner className="size-4" />
                Wird aktualisiert…
              </p>
            )}
            <VehicleForm
              key={formKey}
              initialValues={vehicleToFormValues(vehicle)}
              submitLabel="Änderungen speichern"
              loadingText="Wird gespeichert…"
              submitIcon={<Save className="size-5" aria-hidden />}
              onSubmit={handleSave}
              onEdit={handleEdit}
              readOnly={updating}
            />
          </>
        )}
      </PageContainer>
    </>
  );
}
