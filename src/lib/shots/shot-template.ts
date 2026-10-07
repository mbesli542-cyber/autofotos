/**
 * Shot templates define the photo standard.
 *
 * The guided camera, the review grid, the progress logic and the export
 * naming all read from a template – never from hardcoded lists in components.
 * To change the photo standard, edit (or add) a template here.
 */

export type ShotCategory = "exterior" | "interior" | "detail";

export interface ShotDefinition {
  /** Stable identifier, used in storage paths and export file names. */
  key: string;
  /** 1-based position in the final listing order. */
  order: number;
  /** German title shown to the employee. */
  title: string;
  /** German instruction shown in the camera. */
  instruction: string;
  category: ShotCategory;
  /** Framing guide in /public/overlays, or null (generic grid + corner marks only). */
  overlayAsset: string | null;
  /** Mirror the overlay horizontally (e.g. right side uses the side guide). */
  overlayMirrored?: boolean;
  required: boolean;
}

export interface ShotTemplate {
  id: string;
  name: string;
  shots: readonly ShotDefinition[];
}

export const SHOT_CATEGORY_LABELS: Record<ShotCategory, string> = {
  exterior: "Außen",
  interior: "Innenraum",
  detail: "Details",
};

const POSITION_AS_TEMPLATE = "Positionieren Sie das Fahrzeug wie in der Vorlage.";

export const AUTOEXPERTEN_STANDARD_TEMPLATE: ShotTemplate = {
  id: "autoexperten_standard_v1",
  name: "AutoExperten Standard (15 Fotos)",
  shots: [
    {
      key: "front_left_45",
      order: 1,
      title: "Vorne links (45°)",
      instruction: POSITION_AS_TEMPLATE,
      category: "exterior",
      overlayAsset: "/overlays/front-left-45.svg",
      required: true,
    },
    {
      key: "front",
      order: 2,
      title: "Vorne",
      instruction:
        "Stellen Sie sich mittig vor das Fahrzeug. Das Kennzeichen sollte gerade im Bild sein.",
      category: "exterior",
      overlayAsset: "/overlays/front.svg",
      required: true,
    },
    {
      key: "front_right_45",
      order: 3,
      title: "Vorne rechts (45°)",
      instruction: POSITION_AS_TEMPLATE,
      category: "exterior",
      overlayAsset: "/overlays/front-right-45.svg",
      required: true,
    },
    {
      key: "left_side",
      order: 4,
      title: "Linke Fahrzeugseite",
      instruction:
        "Fotografieren Sie die gesamte linke Seite parallel zum Fahrzeug.",
      category: "exterior",
      overlayAsset: "/overlays/side.svg",
      required: true,
    },
    {
      key: "right_side",
      order: 5,
      title: "Rechte Fahrzeugseite",
      instruction:
        "Fotografieren Sie die gesamte rechte Seite parallel zum Fahrzeug.",
      category: "exterior",
      overlayAsset: "/overlays/side.svg",
      overlayMirrored: true,
      required: true,
    },
    {
      key: "rear_left_45",
      order: 6,
      title: "Hinten links (45°)",
      instruction: POSITION_AS_TEMPLATE,
      category: "exterior",
      overlayAsset: "/overlays/rear-left-45.svg",
      required: true,
    },
    {
      key: "rear",
      order: 7,
      title: "Hinten",
      instruction:
        "Stellen Sie sich mittig hinter das Fahrzeug. Das Kennzeichen sollte gerade im Bild sein.",
      category: "exterior",
      overlayAsset: "/overlays/rear.svg",
      required: true,
    },
    {
      key: "rear_right_45",
      order: 8,
      title: "Hinten rechts (45°)",
      instruction: POSITION_AS_TEMPLATE,
      category: "exterior",
      overlayAsset: "/overlays/rear-right-45.svg",
      required: true,
    },
    {
      key: "cockpit",
      order: 9,
      title: "Cockpit",
      instruction: "Fotografieren Sie das Cockpit möglichst symmetrisch.",
      category: "interior",
      overlayAsset: "/overlays/cockpit.svg",
      required: true,
    },
    {
      key: "front_interior",
      order: 10,
      title: "Innenraum vorne",
      instruction: "Zeigen Sie Sitze, Armaturenbrett und Mittelkonsole.",
      category: "interior",
      overlayAsset: null,
      required: true,
    },
    {
      key: "rear_seats",
      order: 11,
      title: "Fond / Rücksitze",
      instruction:
        "Fotografieren Sie die Rücksitzbank durch die geöffnete hintere Tür.",
      category: "interior",
      overlayAsset: null,
      required: true,
    },
    {
      key: "driver_seat",
      order: 12,
      title: "Fahrersitz",
      instruction:
        "Zeigen Sie den Fahrersitz vollständig bei geöffneter Fahrertür.",
      category: "interior",
      overlayAsset: null,
      required: true,
    },
    {
      key: "door_controls",
      order: 13,
      title: "Tür & Sitzbedienung",
      instruction:
        "Zeigen Sie Türverkleidung, Fensterheber und Sitzverstellung.",
      category: "detail",
      overlayAsset: null,
      required: true,
    },
    {
      key: "wheel_detail",
      order: 14,
      title: "Felge / Rad",
      instruction:
        "Fotografieren Sie das Rad gerade von der Seite und möglichst formatfüllend.",
      category: "detail",
      overlayAsset: "/overlays/wheel.svg",
      required: true,
    },
    {
      key: "special_detail",
      order: 15,
      title: "Fahrzeugdetail",
      instruction:
        "Fotografieren Sie z. B. Motorisierung, Typenschild oder besonderes Ausstattungsmerkmal.",
      category: "detail",
      overlayAsset: null,
      required: true,
    },
  ],
};

export const DEFAULT_SHOT_TEMPLATE = AUTOEXPERTEN_STANDARD_TEMPLATE;

/** Shots sorted by their template order. */
export function getOrderedShots(template: ShotTemplate): ShotDefinition[] {
  return [...template.shots].sort((a, b) => a.order - b.order);
}

export function getRequiredShots(template: ShotTemplate): ShotDefinition[] {
  return getOrderedShots(template).filter((shot) => shot.required);
}

export function getShot(
  template: ShotTemplate,
  key: string,
): ShotDefinition | undefined {
  return template.shots.find((shot) => shot.key === key);
}

/* ------------------------------------------------------------------------ */
/* Additional ("Zusatzfotos") – free photos after the required sequence.     */
/* ------------------------------------------------------------------------ */

export const EXTRA_SHOT_PREFIX = "extra_";
/** Extra photos are ordered after every template shot. */
export const EXTRA_SHOT_ORDER_OFFSET = 100;

export function isExtraShotKey(key: string): boolean {
  return key.startsWith(EXTRA_SHOT_PREFIX);
}

export interface ExtraShotSlot {
  key: string;
  order: number;
  title: string;
}

/** Next free slot for an additional photo, based on existing shot keys. */
export function getNextExtraShot(existingShotKeys: Iterable<string>): ExtraShotSlot {
  let highest = 0;
  for (const key of existingShotKeys) {
    if (!isExtraShotKey(key)) continue;
    const n = Number.parseInt(key.slice(EXTRA_SHOT_PREFIX.length), 10);
    if (Number.isFinite(n) && n > highest) highest = n;
  }
  const next = highest + 1;
  return {
    key: `${EXTRA_SHOT_PREFIX}${String(next).padStart(2, "0")}`,
    order: EXTRA_SHOT_ORDER_OFFSET + next,
    title: `Zusatzfoto ${next}`,
  };
}
