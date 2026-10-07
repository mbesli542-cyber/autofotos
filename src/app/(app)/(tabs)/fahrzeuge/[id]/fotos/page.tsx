import type { Metadata } from "next";
import { Suspense } from "react";
import { PageLoading } from "@/components/ui/Spinner";
import { PhotoReviewScreen } from "@/features/review/PhotoReviewScreen";

export const metadata: Metadata = { title: "Fotos überprüfen" };

async function PhotoReviewRoute({ params }: Pick<PageProps<"/fahrzeuge/[id]/fotos">, "params">) {
  const { id } = await params;
  return <PhotoReviewScreen vehicleId={id} />;
}

export default function PhotoReviewPage(props: PageProps<"/fahrzeuge/[id]/fotos">) {
  return (
    <Suspense fallback={<PageLoading message="Fotos werden geladen…" />}>
      <PhotoReviewRoute params={props.params} />
    </Suspense>
  );
}
