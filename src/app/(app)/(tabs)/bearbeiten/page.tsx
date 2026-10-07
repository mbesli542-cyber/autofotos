import type { Metadata } from "next";
import { VehicleHubScreen } from "@/features/vehicles/VehicleHubScreen";

export const metadata: Metadata = { title: "Bearbeiten" };

export default function ProcessingHubPage() {
  return <VehicleHubScreen hub="processing" />;
}
