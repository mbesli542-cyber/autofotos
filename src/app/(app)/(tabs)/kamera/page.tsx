import type { Metadata } from "next";
import { VehicleHubScreen } from "@/features/vehicles/VehicleHubScreen";

export const metadata: Metadata = { title: "Kamera" };

export default function CameraHubPage() {
  return <VehicleHubScreen hub="camera" />;
}
