import { cn } from "@/lib/cn";

export function ProgressBar({
  value,
  max,
  label,
  className,
  tone = "blue",
}: {
  value: number;
  max: number;
  label: string;
  className?: string;
  tone?: "blue" | "success";
}) {
  const ratio = max === 0 ? 0 : Math.min(1, Math.max(0, value / max));
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-ae-surface-3", className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-300",
          tone === "success" ? "bg-ae-success" : "bg-ae-blue",
        )}
        style={{ width: `${ratio * 100}%` }}
      />
    </div>
  );
}
