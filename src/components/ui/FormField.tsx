import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

const INPUT_CLASSES =
  "w-full rounded-xl border bg-ae-surface-2 px-3.5 text-base text-ae-text placeholder:text-ae-subtle transition-colors focus:border-ae-blue focus:outline-none focus:ring-2 focus:ring-ae-blue/30";

interface FieldShellProps {
  id: string;
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}

function FieldShell({ id, label, required, error, hint, children, className }: FieldShellProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-sm font-medium text-ae-muted">
        {label}
        {required && (
          <span className="ml-0.5 text-ae-blue" aria-hidden>
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-sm text-ae-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-ae-subtle">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function TextField({
  id,
  label,
  required,
  error,
  hint,
  className,
  ...inputProps
}: Omit<FieldShellProps, "children"> & ComponentProps<"input">) {
  return (
    <FieldShell id={id} label={label} required={required} error={error} hint={hint} className={className}>
      <input
        id={id}
        name={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={cn(INPUT_CLASSES, "h-12", error ? "border-ae-danger" : "border-ae-border")}
        {...inputProps}
      />
    </FieldShell>
  );
}

export function TextAreaField({
  id,
  label,
  required,
  error,
  hint,
  className,
  ...textareaProps
}: Omit<FieldShellProps, "children"> & ComponentProps<"textarea">) {
  return (
    <FieldShell id={id} label={label} required={required} error={error} hint={hint} className={className}>
      <textarea
        id={id}
        name={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={cn(INPUT_CLASSES, "min-h-24 py-3", error ? "border-ae-danger" : "border-ae-border")}
        {...textareaProps}
      />
    </FieldShell>
  );
}
