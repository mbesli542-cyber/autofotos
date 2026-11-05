/**
 * "Tipps für perfekte Fotos" – how the photo is taken is the biggest quality
 * lever: the showroom processing keeps every vehicle pixel (reflections,
 * dirt, plate frames stay), and it rejects photos with other objects behind
 * the car or a cropped car. Shown once when the guided camera opens for a
 * new vehicle (re-openable there and on "Mehr"); per-shot hints live in the
 * shot template (`tip`).
 */

export type ShootingTipIcon = "hall" | "clean" | "space" | "frame" | "plate" | "landscape" | "original";

export interface ShootingTip {
  id: string;
  icon: ShootingTipIcon;
  title: string;
  text: string;
}

export const SHOOTING_TIPS: readonly ShootingTip[] = [
  {
    id: "hall",
    icon: "hall",
    title: "In der Halle fotografieren",
    text: "Licht im Rücken, keine direkte Sonne – Spiegelungen im Lack bleiben im Foto.",
  },
  {
    id: "clean",
    icon: "clean",
    title: "Fahrzeug gewaschen",
    text: "Sauber und trocken – Staub und Wasserflecken sind im Inserat sichtbar.",
  },
  {
    id: "space",
    icon: "space",
    title: "2–3 m frei hinter dem Auto",
    text: "Keine anderen Fahrzeuge, Personen, Schilder oder Pfosten direkt dahinter.",
  },
  {
    id: "frame",
    icon: "frame",
    title: "Ganzes Fahrzeug mit Rand",
    text: "Nichts abschneiden – rundum etwas Platz lassen, wie in der Vorlage.",
  },
  {
    id: "plate",
    icon: "plate",
    title: "Kennzeichenhalter und Aufkleber ab",
    text: "Händlerwerbung am Fahrzeug vorher entfernen – sie wird nicht wegretuschiert.",
  },
  {
    id: "landscape",
    icon: "landscape",
    title: "Außen im Querformat",
    text: "Für die 8 Außenfotos das Smartphone quer halten.",
  },
  {
    id: "original",
    icon: "original",
    title: "Nur Kamera-Originale",
    text: "Beim Hochladen keine WhatsApp- oder Chat-Kopien verwenden – sie sind zu klein.",
  },
];

/* ------------------------------------------------------------------------ */
/* When the sheet opens by itself                                            */
/* ------------------------------------------------------------------------ */

export interface ShootingTipsPrefs {
  /** false: "Bei neuen Fahrzeugen nicht mehr automatisch zeigen". */
  autoShow: boolean;
  /** Vehicles the tips were already shown for (newest last). */
  seenVehicleIds: readonly string[];
}

export const DEFAULT_SHOOTING_TIPS_PREFS: ShootingTipsPrefs = { autoShow: true, seenVehicleIds: [] };

/** Vehicles remembered (the oldest are forgotten first). */
const MAX_SEEN = 50;

const STORAGE_KEY = "ae-photo.shooting-tips.v1";

/** Opens by itself when the camera opens for a vehicle without any photo – once per vehicle. */
export function shouldShowShootingTips(prefs: ShootingTipsPrefs, vehicleId: string, isNewVehicle: boolean): boolean {
  return isNewVehicle && prefs.autoShow && !prefs.seenVehicleIds.includes(vehicleId);
}

/** The tips were shown for `vehicleId` (`autoShow`: the employee's choice in the sheet). */
export function markShootingTipsSeen(prefs: ShootingTipsPrefs, vehicleId: string, autoShow = prefs.autoShow): ShootingTipsPrefs {
  const seen = [...prefs.seenVehicleIds.filter((id) => id !== vehicleId), vehicleId].slice(-MAX_SEEN);
  return { autoShow, seenVehicleIds: seen };
}

export function parseShootingTipsPrefs(raw: string | null): ShootingTipsPrefs {
  if (!raw) return DEFAULT_SHOOTING_TIPS_PREFS;
  try {
    const value = JSON.parse(raw) as Partial<Record<keyof ShootingTipsPrefs, unknown>> | null;
    const seen = Array.isArray(value?.seenVehicleIds)
      ? value.seenVehicleIds.filter((id): id is string => typeof id === "string").slice(-MAX_SEEN)
      : [];
    return { autoShow: value?.autoShow !== false, seenVehicleIds: seen };
  } catch {
    return DEFAULT_SHOOTING_TIPS_PREFS;
  }
}

/** This device's choice (defaults when storage is blocked). */
export function readShootingTipsPrefs(): ShootingTipsPrefs {
  try {
    return parseShootingTipsPrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_SHOOTING_TIPS_PREFS;
  }
}

export function writeShootingTipsPrefs(prefs: ShootingTipsPrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage blocked or full: the tips simply open again next time.
  }
}
