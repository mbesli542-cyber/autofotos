import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-ae-blue text-white shadow-[var(--shadow-blue)] hover:bg-ae-blue-strong active:bg-ae-blue-strong",
  secondary:
    "bg-ae-surface-2 text-ae-text border border-ae-border hover:bg-ae-surface-3 hover:border-ae-border-strong",
  ghost: "text-ae-muted hover:text-ae-text hover:bg-ae-surface-2",
  danger: "bg-ae-danger/12 text-ae-danger border border-ae-danger/35 hover:bg-ae-danger/20",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-9 px-3 text-sm rounded-lg",
  md: "h-11 px-4 text-[15px] rounded-xl",
  lg: "h-14 px-5 text-base rounded-xl",
};

export function buttonClasses({
  variant = "primary",
  size = "md",
  fullWidth = false,
  className,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
}): string {
  return cn(
    "inline-flex select-none items-center justify-center gap-2 font-semibold transition-[background-color,border-color,color,transform] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45 aria-disabled:pointer-events-none aria-disabled:opacity-45",
    VARIANTS[variant],
    SIZES[size],
    fullWidth && "w-full",
    className,
  );
}

interface CommonProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  icon?: ReactNode;
}

export function Button({
  variant,
  size,
  fullWidth,
  icon,
  className,
  children,
  type = "button",
  ...props
}: CommonProps & ComponentProps<"button">) {
  return (
    <button
      type={type}
      className={buttonClasses({ variant, size, fullWidth, className })}
      {...props}
    >
      {icon}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant,
  size,
  fullWidth,
  icon,
  className,
  children,
  ...props
}: CommonProps & ComponentProps<typeof Link>) {
  return (
    <Link className={buttonClasses({ variant, size, fullWidth, className })} {...props}>
      {icon}
      {children}
    </Link>
  );
}
