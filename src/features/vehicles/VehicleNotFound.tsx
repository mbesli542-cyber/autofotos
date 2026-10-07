import { SearchX } from "lucide-react";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

export function VehicleNotFound() {
  return (
    <EmptyState
      icon={<SearchX className="size-7" aria-hidden />}
      title="Fahrzeug nicht gefunden"
      description="Das Fahrzeug existiert nicht oder Sie haben keinen Zugriff darauf."
      action={
        <ButtonLink href="/fahrzeuge" variant="secondary" fullWidth>
          Zur Fahrzeugliste
        </ButtonLink>
      }
    />
  );
}
