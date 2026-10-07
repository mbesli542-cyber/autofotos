import type { ComponentProps } from "react";
import { Button } from "./Button";
import { Spinner } from "./Spinner";

/** Button that shows a spinner and a German progress text while busy. */
export function LoadingButton({
  loading,
  loadingText,
  children,
  disabled,
  icon,
  ...props
}: ComponentProps<typeof Button> & { loading: boolean; loadingText?: string }) {
  return (
    <Button
      {...props}
      icon={loading ? <Spinner /> : icon}
      disabled={disabled || loading}
      aria-busy={loading}
    >
      {loading ? (loadingText ?? children) : children}
    </Button>
  );
}
