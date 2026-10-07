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
    /** "Experten" blue – primary actions and active states. */
    blue: "#0A7BFF",
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
 * Logo assets.
 *
 * PLACEHOLDER: The official AutoExperten logo files are not in the repository
 * yet. Until they are, <BrandLogo /> renders a text-based placeholder
 * ("Auto" + "Experten"). To switch to the real logo:
 *   1. Put the files into /public/brand/ using the names below.
 *   2. Set `useAssetFiles` to `true`.
 * No UI code has to change.
 */
export const LOGO_ASSETS = {
  useAssetFiles: false,
  /** Logo for light backgrounds ("Auto" dark, "Experten" blue). */
  onLight: "/brand/autoexperten-logo.svg",
  /** Logo for dark backgrounds ("Auto" light, "Experten" blue). */
  onDark: "/brand/autoexperten-logo-light.svg",
} as const;
