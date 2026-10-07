import type { Metadata } from "next";
import { Suspense } from "react";
import { PageLoading } from "@/components/ui/Spinner";
import { VehicleDetailScreen } from "@/features/vehicles/VehicleDetailScreen";

export const metadata: Metadata = { title: "Fahrzeug" };

async function VehicleDetailRoute({
  params,
  searchParams,
}: Pick<PageProps<"/fahrzeuge/[id]">, "params" | "searchParams">) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  return (
    <VehicleDetailScreen
      vehicleId={id}
      initialVariant={query.ansicht === "bearbeitet" ? "processed" : "original"}
    />
  );
}

export default function VehicleDetailPage(props: PageProps<"/fahrzeuge/[id]">) {
  return (
    <Suspense fallback={<PageLoading message="Fahrzeug wird geladen…" />}>
      <VehicleDetailRoute params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}
