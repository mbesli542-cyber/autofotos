import { LOGO_ASSETS } from "@/config/brand";
import { cn } from "@/lib/cn";

const SIZES = {
  sm: { text: "text-lg", img: "h-5" },
  md: { text: "text-xl", img: "h-6" },
  lg: { text: "text-[2rem] leading-none", img: "h-9" },
} as const;

/**
 * AutoExperten logo – renders the official logo files from /public/brand/
 * (see LOGO_ASSETS in src/config/brand.ts). The text fallback below is only
 * used if `useAssetFiles` is switched off.
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
        width={LOGO_ASSETS.width}
        height={LOGO_ASSETS.height}
        decoding="async"
        className={cn("w-auto select-none", SIZES[size].img, className)}
        draggable={false}
      />
    );
  }

  // Text fallback (only when LOGO_ASSETS.useAssetFiles is false).
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
