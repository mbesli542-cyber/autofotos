import { LOGO_ASSETS } from "@/config/brand";
import { cn } from "@/lib/cn";

const SIZES = {
  sm: { text: "text-lg", img: "h-5" },
  md: { text: "text-xl", img: "h-6" },
  lg: { text: "text-[2rem] leading-none", img: "h-9" },
} as const;

/**
 * AutoExperten logo.
 *
 * PLACEHOLDER: renders a text wordmark ("Auto" + "Experten" in blue) until
 * the official logo files are added to /public/brand/ and
 * `LOGO_ASSETS.useAssetFiles` is set to true (src/config/brand.ts).
 * Callers never need to change.
 */
export function BrandLogo({
  tone = "onDark",
  size = "md",
  className,
}: {
  /** Background the logo sits on. */
  tone?: "onDark" | "onLight";
  size?: keyof typeof SIZES;
  className?: string;
}) {
  if (LOGO_ASSETS.useAssetFiles) {
    return (
      <img
        src={tone === "onDark" ? LOGO_ASSETS.onDark : LOGO_ASSETS.onLight}
        alt="AutoExperten"
        className={cn("w-auto", SIZES[size].img, className)}
      />
    );
  }

  // --- PLACEHOLDER WORDMARK (replace by setting LOGO_ASSETS.useAssetFiles) ---
  return (
    <span
      className={cn("inline-flex font-bold tracking-tight", SIZES[size].text, className)}
      role="img"
      aria-label="AutoExperten"
    >
      <span className={tone === "onDark" ? "text-white" : "text-[#0B0C0E]"} aria-hidden>
        Auto
      </span>
      <span className="text-ae-blue" aria-hidden>
        Experten
      </span>
    </span>
  );
}
