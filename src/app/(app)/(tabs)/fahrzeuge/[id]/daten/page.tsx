import type { Metadata } from "next";
import { Suspense } from "react";
import { PageLoading } from "@/components/ui/Spinner";
import { EditVehicleScreen } from "@/features/vehicles/EditVehicleScreen";

export const metadata: Metadata = { title: "Fahrzeugdaten" };

async function EditVehicleRoute({ params }: Pick<PageProps<"/fahrzeuge/[id]/daten">, "params">) {
  const { id } = await params;
  return <EditVehicleScreen vehicleId={id} />;
}

export default function EditVehiclePage(props: PageProps<"/fahrzeuge/[id]/daten">) {
  return (
    <Suspense fallback={<PageLoading message="Fahrzeug wird geladen…" />}>
      <EditVehicleRoute params={props.params} />
    </Suspense>
  );
}
