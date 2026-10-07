/**
 * True while no real processing backend is connected. Used only for UI hints.
 * Set NEXT_PUBLIC_IMAGE_PROCESSOR=real together with IMAGE_PROCESSOR=real.
 */
export const PROCESSING_IS_SIMULATED = process.env.NEXT_PUBLIC_IMAGE_PROCESSOR !== "real";
