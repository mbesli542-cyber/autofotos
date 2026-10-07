import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center rounded-2xl border border-dashed border-ae-border-strong bg-ae-surface/60 px-6 py-10 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-ae-blue-soft text-ae-blue">
          {icon}
        </div>
      )}
      <h2 className="text-base font-semibold">{title}</h2>
      {description && <p className="mt-1.5 max-w-sm text-sm text-ae-muted">{description}</p>}
      {action && <div className="mt-5 w-full max-w-xs">{action}</div>}
    </div>
  );
}
