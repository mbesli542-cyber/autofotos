import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <LoaderCircle
      className={cn("size-5 animate-spin", className)}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "status" : undefined}
    />
  );
}

/** Centered loading indicator with a German message. */
export function PageLoading({ message = "Wird geladen…" }: { message?: string }) {
  return (
    <div
      className="flex min-h-[40vh] flex-col items-center justify-center gap-3 text-ae-muted"
      role="status"
      aria-live="polite"
    >
      <Spinner className="size-7 text-ae-blue" />
      <p className="text-sm">{message}</p>
    </div>
  );
}
