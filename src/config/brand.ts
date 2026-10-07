/**
 * AutoExperten brand configuration.
 *
 * Single source of truth for company data and brand colours. Used by the UI,
 * the PWA manifest and the (future) image processing presets.
 */
export const BRAND = {
  companyName: "AutoExperten Schwetzingen",
  shortName: "AutoExperten",
  city: "Schwetzingen",
  country: "Deutschland",
  website: "https://www.autoexperten-rn.de",
  websiteLabel: "www.autoexperten-rn.de",
  phone: "+49 6202 9262357",
  phoneHref: "tel:+4962029262357",
  colors: {
    /** UI accent blue – primary actions and active states. */
    blue: "#0A7BFF",
    /** Colours measured from the official logo file (public/brand/official). */
    logoBlue: "#0788EA",
    logoGray: "#403F3F",
    /** "Auto" on light backgrounds. */
    dark: "#0B0C0E",
    /** App background (near black / graphite). */
    background: "#0B0C0E",
  },
} as const;

export const APP_INFO = {
  name: "AutoExperten Photo",
  shortName: "AE Photo",
  description:
    "Interne Fahrzeugfotografie für AutoExperten Schwetzingen – geführte Aufnahmen für Fahrzeuginserate.",
  version: "0.1.0",
} as const;

/**
 * Official logo assets (public/brand/).
 *
 * Source: https://www.autoexperten-rn.de (AutoExperten_Logo.png and the
 * "AE" app icon), stored byte-identical in public/brand/official/.
 * The website only provides raster artwork (PNG), no SVG.
 *
 * - onLight: official wordmark, transparent margin trimmed, scaled for UI use.
 * - onDark:  same pixels, only the neutral-gray "Auto" letters recoloured to
 *            white for dark backgrounds (blue untouched). Replace with an
 *            official light version as soon as one exists.
 */
export const LOGO_ASSETS = {
  useAssetFiles: true,
  onLight: "/brand/autoexperten-logo.png",
  onDark: "/brand/autoexperten-logo-light.png",
  /** Intrinsic size of the UI logo files (for layout without shifts). */
  width: 760,
  height: 128,
  official: {
    wordmark: "/brand/official/AutoExperten_Logo.png",
    icon: "/brand/official/AutoExperten_Icon.png",
  },
} as const;
