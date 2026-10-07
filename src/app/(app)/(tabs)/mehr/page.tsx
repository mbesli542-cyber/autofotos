import type { Metadata } from "next";
import { MoreScreen } from "@/features/more/MoreScreen";

export const metadata: Metadata = { title: "Mehr" };

export default function MorePage() {
  return <MoreScreen />;
}
