/**
 * "Tipps für perfekte Fotos" – how the photo is taken is the biggest quality
 * lever: the showroom processing keeps every vehicle pixel (reflections,
 * dirt, plate frames stay), and it rejects photos with other objects behind
 * the car or a cropped car. Shown by itself once per device, when the guided
 * camera opens for a new vehicle (re-openable there and on "Mehr"); per-shot
 * hints live in the shot template (`tip`).
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
    text: "Licht im Rücken, keine direkte Sonne – Spiegelungen bleiben im Lack.",
  },
  {
    id: "clean",
    icon: "clean",
    title: "Fahrzeug gewaschen",
    text: "Staub und Wasserflecken sieht man im Inserat.",
  },
  {
    id: "space",
    icon: "space",
    title: "2–3 m frei hinter dem Auto",
    text: "Keine Autos, Personen, Schilder oder Pfosten direkt dahinter.",
  },
  {
    id: "frame",
    icon: "frame",
    title: "Ganzes Fahrzeug mit Rand",
    text: "Nichts abschneiden – rundum etwas Platz wie in der Vorlage.",
  },
  {
    id: "plate",
    icon: "plate",
    title: "Kennzeichenhalter und Aufkleber ab",
    text: "Händlerwerbung vorher entfernen – sie wird nicht wegretuschiert.",
  },
  {
    id: "landscape",
    icon: "landscape",
    title: "Außen im Querformat",
    text: "Für alle 8 Außenfotos das Smartphone quer halten.",
  },
  {
    id: "original",
    icon: "original",
    title: "Nur Kamera-Originale",
    text: "Keine WhatsApp- oder Chat-Kopien hochladen – sie sind zu klein.",
  },
];

/* ------------------------------------------------------------------------ */
/* When the sheet opens by itself                                            */
/* ------------------------------------------------------------------------ */

export interface ShootingTipsPrefs {
  /**
   * The sheet already opened by itself on this device. It does that ONCE –
   * afterwards only the light bulb in the camera and "Mehr" show it, so daily
   * use costs no extra tap.
   */
  autoShown: boolean;
}

export const DEFAULT_SHOOTING_TIPS_PREFS: ShootingTipsPrefs = { autoShown: false };

const STORAGE_KEY = "ae-photo.shooting-tips.v1";

/** Opens by itself when the camera opens for a vehicle without any photo – once per device. */
export function shouldShowShootingTips(prefs: ShootingTipsPrefs, isNewVehicle: boolean): boolean {
  return isNewVehicle && !prefs.autoShown;
}

export function parseShootingTipsPrefs(raw: string | null): ShootingTipsPrefs {
  if (!raw) return DEFAULT_SHOOTING_TIPS_PREFS;
  try {
    const value = JSON.parse(raw) as { autoShown?: unknown; autoShow?: unknown } | null;
    // `autoShow: false`: the earlier "nicht mehr automatisch zeigen" choice.
    return { autoShown: value?.autoShown === true || value?.autoShow === false };
  } catch {
    return DEFAULT_SHOOTING_TIPS_PREFS;
  }
}

/** This device's state (defaults when storage is blocked). */
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
