import type { Metadata } from "next";
import { Suspense } from "react";
import { PageLoading } from "@/components/ui/Spinner";
import { ProcessingScreen } from "@/features/processing/ProcessingScreen";

export const metadata: Metadata = { title: "Fotos bearbeiten" };

async function ProcessingRoute({ params }: Pick<PageProps<"/fahrzeuge/[id]/bearbeiten">, "params">) {
  const { id } = await params;
  return <ProcessingScreen vehicleId={id} />;
}

export default function ProcessingPage(props: PageProps<"/fahrzeuge/[id]/bearbeiten">) {
  return (
    <Suspense fallback={<PageLoading message="Fotos werden geladen…" />}>
      <ProcessingRoute params={props.params} />
    </Suspense>
  );
}
