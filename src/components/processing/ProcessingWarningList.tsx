import { Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/cn";
import type { ProcessingWarning } from "@/lib/domain/types";
import { infoWarnings, warningsToCheck } from "@/lib/processing/processing-warnings";

/**
 * The processor's hints on a processed result, in its own German words:
 * hints that ask for a check or a retake in amber (first), informational
 * ones subtly. A long word (e.g. a file name in a future hint) wraps
 * anywhere instead of widening the page. Renders nothing without hints.
 */
export function ProcessingWarningList({
  warnings,
  className,
}: {
  warnings: readonly ProcessingWarning[];
  className?: string;
}) {
  const check = warningsToCheck(warnings);
  const info = infoWarnings(warnings);
  if (check.length === 0 && info.length === 0) return null;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      {check.map((warning, index) => (
        <p key={`check-${index}`} className="flex items-start gap-1.5 text-xs text-ae-warning">
          <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">{warning.message}</span>
        </p>
      ))}
      {info.map((warning, index) => (
        <p key={`info-${index}`} className="flex items-start gap-1.5 text-xs text-ae-subtle">
          <Info className="mt-px size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">{warning.message}</span>
        </p>
      ))}
    </div>
  );
}
