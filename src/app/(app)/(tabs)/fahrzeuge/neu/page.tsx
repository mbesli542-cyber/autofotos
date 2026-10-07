import type { Metadata } from "next";
import { NewVehicleScreen } from "@/features/vehicles/NewVehicleScreen";

export const metadata: Metadata = { title: "Neues Fahrzeug" };

export default function NewVehiclePage() {
  return <NewVehicleScreen />;
}
