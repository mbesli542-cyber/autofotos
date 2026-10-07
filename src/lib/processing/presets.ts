/**
 * Processing presets ("Bearbeitungsstile").
 *
 * These are CONFIGURATION for the future processing pipeline – nothing here
 * transforms images yet. The production pipeline will read the showroom spec
 * to composite the segmented, untouched vehicle into the AutoExperten
 * showroom. See README → "Zukünftige Bildverarbeitung".
 */
import { BRAND, LOGO_ASSETS } from "@/config/brand";
import type { ProcessingPresetId } from "@/lib/domain/types";

export interface ShowroomSpec {
  ambience: "bright" | "dark";
  wall: { description: string; color: string };
  floor: { description: string; material: "wood_parquet" | "polished_concrete"; tone: string };
  ceilingLighting: { description: string; colorTemperatureK: number };
  accentLighting: { description: string; type: "vertical_led"; color: string };
  /** Decorative interior elements visible in the reference showroom. */
  decor: readonly string[];
  brandWall: {
    logoAsset: string;
    lines: readonly string[];
    website: string;
    phone: string;
  };
  /** Reference photo the final look must match (supplied by AutoExperten). */
  referenceImage: string;
  /** Vehicle placement inside the output frame (normalised 0..1). */
  vehiclePlacement: {
    horizontalCenter: number;
    groundLine: number;
    targetWidthRatio: number;
  };
  contactShadow: { opacity: number; softness: "soft" | "medium" | "hard" };
}

export interface ProcessingPreset {
  id: ProcessingPresetId;
  name: string;
  description: string;
  /** Replace the background with a showroom, or keep the original one. */
  background: "showroom" | "original";
  showroom: ShowroomSpec | null;
  adjustments: {
    lighting: boolean;
    color: boolean;
    contrast: boolean;
    /** Vehicle paint colour must stay truthful – never re-coloured. */
    preserveVehicleColor: true;
  };
  /** Vehicle pixels are never regenerated. Always true – documented on purpose. */
  preserveOriginalVehiclePixels: true;
  output: { format: "jpeg"; quality: number; aspectRatio: "4:3" | "original"; maxLongEdge: number };
  /** CSS used for the preset card preview in the UI. */
  uiPreview: { background: string; accent: string; textTone: "light" | "dark" };
}

const BRAND_WALL = {
  logoAsset: LOGO_ASSETS.onLight,
  lines: ["AutoExperten", BRAND.city],
  website: BRAND.websiteLabel,
  phone: BRAND.phone,
} as const;

export const PROCESSING_PRESETS: Record<ProcessingPresetId, ProcessingPreset> = {
  autoexperten_standard: {
    id: "autoexperten_standard",
    name: "AutoExperten Standard",
    description:
      "Heller Premium-Showroom, Holzfußboden, AutoExperten Wandlogo und blaue Lichtakzente.",
    background: "showroom",
    showroom: {
      ambience: "bright",
      wall: { description: "Saubere weiße / hellgraue Wand", color: "#EEF0F3" },
      floor: {
        description: "Hochwertiger warmer Holz-/Parkettboden",
        material: "wood_parquet",
        tone: "#A8784C",
      },
      ceilingLighting: {
        description: "Warme Deckenspots",
        colorTemperatureK: 3000,
      },
      accentLighting: {
        description: "Blaue vertikale LED-Lichtleisten",
        type: "vertical_led",
        color: BRAND.colors.blue,
      },
      decor: [
        "Vertikale Holzlamellen-Wandpaneele",
        "Grünpflanzen (Palmen) in schlichten Pflanzkübeln",
      ],
      brandWall: BRAND_WALL,
      referenceImage: "/presets/autoexperten-standard-reference.jpg",
      vehiclePlacement: { horizontalCenter: 0.5, groundLine: 0.84, targetWidthRatio: 0.8 },
      contactShadow: { opacity: 0.55, softness: "medium" },
    },
    adjustments: { lighting: true, color: true, contrast: true, preserveVehicleColor: true },
    preserveOriginalVehiclePixels: true,
    output: { format: "jpeg", quality: 0.92, aspectRatio: "4:3", maxLongEdge: 3200 },
    uiPreview: {
      background:
        "linear-gradient(180deg, #f4f5f7 0%, #e7e9ed 58%, #b98a5e 58%, #8f6440 100%)",
      accent: BRAND.colors.blue,
      textTone: "dark",
    },
  },
  autoexperten_dark: {
    id: "autoexperten_dark",
    name: "AutoExperten Dark",
    description: "Dunkler Premium-Showroom mit hochwertiger Lichtstimmung.",
    background: "showroom",
    showroom: {
      ambience: "dark",
      wall: { description: "Dunkle Graphit-Wand", color: "#16181C" },
      floor: {
        description: "Dunkler polierter Boden mit dezenter Spiegelung",
        material: "polished_concrete",
        tone: "#1E2126",
      },
      ceilingLighting: { description: "Gezielte Spots, hoher Kontrast", colorTemperatureK: 4000 },
      accentLighting: {
        description: "Blaue vertikale LED-Lichtleisten",
        type: "vertical_led",
        color: BRAND.colors.blue,
      },
      decor: ["Dezente Holzlamellen-Akzente"],
      brandWall: { ...BRAND_WALL, logoAsset: LOGO_ASSETS.onDark },
      referenceImage: "/presets/autoexperten-dark-reference.jpg",
      vehiclePlacement: { horizontalCenter: 0.5, groundLine: 0.84, targetWidthRatio: 0.8 },
      contactShadow: { opacity: 0.7, softness: "medium" },
    },
    adjustments: { lighting: true, color: true, contrast: true, preserveVehicleColor: true },
    preserveOriginalVehiclePixels: true,
    output: { format: "jpeg", quality: 0.92, aspectRatio: "4:3", maxLongEdge: 3200 },
    uiPreview: {
      background:
        "radial-gradient(120% 70% at 50% 0%, #2a2f38 0%, #121418 60%), #0e1013",
      accent: BRAND.colors.blue,
      textTone: "light",
    },
  },
  original_plus: {
    id: "original_plus",
    name: "Original+",
    description:
      "Originaler Hintergrund bleibt erhalten. Nur Licht, Farbe und Kontrast werden optimiert.",
    background: "original",
    showroom: null,
    adjustments: { lighting: true, color: true, contrast: true, preserveVehicleColor: true },
    preserveOriginalVehiclePixels: true,
    output: { format: "jpeg", quality: 0.92, aspectRatio: "original", maxLongEdge: 4096 },
    uiPreview: {
      background: "linear-gradient(135deg, #5b6470 0%, #8a939f 45%, #3b4048 100%)",
      accent: "#ffffff",
      textTone: "light",
    },
  },
};

export const DEFAULT_PROCESSING_PRESET: ProcessingPresetId = "autoexperten_standard";

export const PROCESSING_PRESET_LIST: readonly ProcessingPreset[] = [
  PROCESSING_PRESETS.autoexperten_standard,
  PROCESSING_PRESETS.autoexperten_dark,
  PROCESSING_PRESETS.original_plus,
];
