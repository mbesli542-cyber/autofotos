import type { Metadata } from "next";
import { VehicleListScreen } from "@/features/vehicles/VehicleListScreen";

export const metadata: Metadata = { title: "Fahrzeuge" };

export default function VehiclesPage() {
  return <VehicleListScreen />;
}
