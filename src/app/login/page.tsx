import type { Metadata } from "next";
import { LoginScreen } from "@/features/auth/LoginScreen";

export const metadata: Metadata = { title: "Anmelden" };

export default function LoginPage() {
  return <LoginScreen />;
}
