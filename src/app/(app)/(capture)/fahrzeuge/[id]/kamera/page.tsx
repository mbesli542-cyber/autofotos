import type { Metadata } from "next";
import { Suspense } from "react";
import { Spinner } from "@/components/ui/Spinner";
import { CameraScreen } from "@/features/camera/CameraScreen";
import { parseCameraReturnTarget } from "@/lib/camera/camera-links";

export const metadata: Metadata = { title: "Kamera" };

function CameraFallback() {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-black" role="status">
      <Spinner className="size-7 text-ae-blue" label="Kamera wird vorbereitet…" />
    </div>
  );
}

async function CameraRoute({
  params,
  searchParams,
}: Pick<PageProps<"/fahrzeuge/[id]/kamera">, "params" | "searchParams">) {
  const [{ id }, query] = await Promise.all([params, searchParams]);
  const shot = typeof query.shot === "string" ? query.shot : null;
  const returnTo = parseCameraReturnTarget(query.zurueck);
  return <CameraScreen vehicleId={id} initialShotKey={shot} returnTo={returnTo} />;
}

/** /fahrzeuge/[id]/kamera?shot=front&zurueck=fotos|bearbeiten (see src/lib/camera/camera-links.ts) */
export default function CameraPage(props: PageProps<"/fahrzeuge/[id]/kamera">) {
  return (
    <Suspense fallback={<CameraFallback />}>
      <CameraRoute params={props.params} searchParams={props.searchParams} />
    </Suspense>
  );
}
