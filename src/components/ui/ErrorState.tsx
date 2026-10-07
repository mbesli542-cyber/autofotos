import { TriangleAlert, RefreshCw } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

export function ErrorState({
  title = "Verbindung fehlgeschlagen.",
  message,
  onRetry,
  className,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center rounded-2xl border border-ae-danger/30 bg-ae-danger/8 px-6 py-8 text-center",
        className,
      )}
    >
      <TriangleAlert className="mb-3 size-8 text-ae-danger" aria-hidden />
      <h2 className="font-semibold">{title}</h2>
      {message && <p className="mt-1 max-w-sm text-sm text-ae-muted">{message}</p>}
      {onRetry && (
        <Button
          variant="secondary"
          className="mt-5"
          onClick={onRetry}
          icon={<RefreshCw className="size-4" aria-hidden />}
        >
          Erneut versuchen
        </Button>
      )}
    </div>
  );
}
