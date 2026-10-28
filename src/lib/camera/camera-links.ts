/**
 * Links into the guided camera for ONE shot and back (pure).
 *
 *   /fahrzeuge/[id]/kamera?shot=front&zurueck=fotos       retake from "Fotos überprüfen"
 *   /fahrzeuge/[id]/kamera?shot=front&zurueck=bearbeiten  retake of a shot the
 *                                                         processor rejected ("Fotos bearbeiten")
 *
 * With a return target the camera goes back there after the photo was taken
 * (and when it is closed) instead of advancing to the next missing shot.
 */

export const CAMERA_RETURN_TARGETS = ["fotos", "bearbeiten"] as const;

export type CameraReturnTarget = (typeof CAMERA_RETURN_TARGETS)[number];

/** The `zurueck` query parameter → return target, null for anything else. */
export function parseCameraReturnTarget(value: unknown): CameraReturnTarget | null {
  return CAMERA_RETURN_TARGETS.find((target) => target === value) ?? null;
}

export function cameraHref(
  vehicleId: string,
  options: { shot?: string | null; returnTo?: CameraReturnTarget | null } = {},
): string {
  const base = `/fahrzeuge/${encodeURIComponent(vehicleId)}/kamera`;
  const query = new URLSearchParams();
  if (options.shot) query.set("shot", options.shot);
  if (options.returnTo) query.set("zurueck", options.returnTo);
  const search = query.toString();
  return search ? `${base}?${search}` : base;
}

/** Where the camera returns to: the review or processing screen, or (null) the vehicle. */
export function cameraReturnHref(vehicleId: string, returnTo: CameraReturnTarget | null): string {
  const base = `/fahrzeuge/${encodeURIComponent(vehicleId)}`;
  return returnTo ? `${base}/${returnTo}` : base;
}

/**
 * Whether a history entry (absolute URL) shows the page `href` leads to from
 * `currentUrl` – same origin and path, any query. Leaving the camera then
 * goes back to that entry instead of adding the page a second time.
 */
export function isEntryForPage(entryUrl: string | null | undefined, href: string, currentUrl: string): boolean {
  if (!entryUrl) return false;
  try {
    const entry = new URL(entryUrl);
    const target = new URL(href, currentUrl);
    return entry.origin === target.origin && entry.pathname === target.pathname;
  } catch {
    return false;
  }
}
