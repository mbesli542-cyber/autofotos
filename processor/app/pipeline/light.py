"""STEP 9 – conservative light matching for the VEHICLE layer.

Truthfulness rules (non-negotiable):
- no hue changes, no repainting, no saturation shifts;
- only a tiny exposure correction, an optional tiny contrast correction and a
  subtle white-balance harmonisation are allowed;
- HARD_LIMITS cap every correction regardless of configuration;
- exposure is applied ratio-preserving (every pixel's R:G:B ratio – hue and
  saturation – stays exactly the same) with a highlight shoulder, so bright
  paint is never clipped to flat white;
- a colour guard measures hue (95th percentile), saturation and newly clipped
  highlights of the vehicle before/after and reverts the correction if the
  paint would change.

Example: a dark navy blue car must stay dark navy blue – never become black.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..presets import VehicleAdjustments
from .color import hue_degrees, lab_from_linear, linear_saturation, luminance, srgb_to_linear

#: Absolute maxima – configuration values above these are ignored.
HARD_LIMITS = {
    "exposure_ev": 0.5,
    "contrast": 0.10,
    "white_balance": 0.05,
    "light_wrap": 0.15,
}

#: Colour guard thresholds.
MAX_HUE_SHIFT_P95_DEG = 1.5  # 95th percentile of |Δhue| over chromatic paint pixels
MAX_SATURATION_CHANGE = 0.03  # relative
MAX_NEW_CLIPPED_FRACTION = 0.002  # vehicle pixels that newly reach full white

#: Photos are corrected only halfway towards a neutral exposure.
EXPOSURE_STRENGTH = 0.5
NEUTRAL_KEY = 0.18


@dataclass
class VehicleCorrection:
    exposure_ev: float = 0.0
    contrast: float = 0.0
    wb_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    #: Filled by the guard.
    guard_passed: bool = True
    reverted: str | None = None
    hue_shift_p95_deg: float = 0.0
    saturation_change: float = 0.0
    new_clipped_fraction: float = 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["wb_gains"] = [round(g, 4) for g in self.wb_gains]
        for key in ("exposure_ev", "contrast", "hue_shift_p95_deg", "saturation_change", "new_clipped_fraction"):
            data[key] = round(float(data[key]), 4)
        return data


def effective_limits(cfg: VehicleAdjustments) -> dict[str, float]:
    return {
        "exposure_ev": float(min(abs(cfg.exposure_ev_limit), HARD_LIMITS["exposure_ev"])),
        "contrast": float(min(abs(cfg.contrast_limit), HARD_LIMITS["contrast"])),
        "white_balance": float(min(abs(cfg.white_balance_limit), HARD_LIMITS["white_balance"])),
        "light_wrap": float(min(abs(cfg.light_wrap), HARD_LIMITS["light_wrap"])),
    }


def _sample(array: np.ndarray, limit: int = 250_000) -> np.ndarray:
    if len(array) <= limit:
        return array
    step = int(np.ceil(len(array) / limit))
    return array[::step]


def estimate_correction(
    photo_rgb: np.ndarray, vehicle_alpha: np.ndarray, cfg: VehicleAdjustments
) -> VehicleCorrection:
    """Plan the vehicle correction from the ORIGINAL photo (pure measurement)."""
    limits = effective_limits(cfg)
    step = max(1, int(max(photo_rgb.shape[:2]) / 1024))
    small = srgb_to_linear(photo_rgb[::step, ::step])
    alpha_small = vehicle_alpha[::step, ::step]

    # Exposure: judged from the SURROUNDINGS only (median luminance of the
    # non-vehicle pixels), so the paint colour never drives it – a black car
    # must not be brightened, a white car must not be darkened. Only
    # under-exposed photos are lifted (half-way, capped); a bright scene is
    # never darkened because the car is placed into a bright showroom.
    ev = 0.0
    surroundings = luminance(small)[alpha_small < 0.1]
    if surroundings.size >= 0.05 * alpha_small.size:
        key = float(np.median(surroundings))
        if key < NEUTRAL_KEY:
            ev = EXPOSURE_STRENGTH * float(np.log2(NEUTRAL_KEY / max(key, 1e-4)))
            ev = float(np.clip(ev, 0.0, limits["exposure_ev"]))

    # White balance: only from NEUTRAL vehicle parts (tyres, glass, chrome).
    gains = (1.0, 1.0, 1.0)
    if limits["white_balance"] > 0:
        vehicle = _sample(small[alpha_small > 0.9])
        if len(vehicle) > 500:
            lab = lab_from_linear(vehicle.reshape(-1, 1, 3)).reshape(-1, 3)
            chroma = np.hypot(lab[:, 1], lab[:, 2])
            neutral = (chroma < 6.0) & (lab[:, 0] > 15.0) & (lab[:, 0] < 90.0)
            if neutral.sum() > max(200, 0.02 * len(vehicle)):
                mean = vehicle[neutral].mean(axis=0)
                gray = float(mean.mean())
                raw = gray / np.maximum(mean, 1e-5)
                raw = 1.0 + 0.5 * (raw - 1.0)  # half-strength
                # keep luminance unchanged, then shrink the deviation (which keeps the
                # luminance neutral) so that no channel exceeds the limit
                lim = limits["white_balance"]
                normalised = raw / float(np.dot(raw, [0.2126, 0.7152, 0.0722]))
                deviation = normalised - 1.0
                largest = float(np.max(np.abs(deviation)))
                if largest > lim:
                    deviation *= lim / largest
                gains = tuple(float(1.0 + d) for d in deviation)
    # Contrast is never estimated automatically: a global contrast change alters how
    # dark paint reads (navy towards black). It stays 0 unless set explicitly.
    return VehicleCorrection(exposure_ev=ev, contrast=0.0, wb_gains=gains)


def apply_correction(linear: np.ndarray, correction: VehicleCorrection) -> np.ndarray:
    out = (linear * np.asarray(correction.wb_gains, np.float32)).astype(np.float32)
    if correction.exposure_ev:
        # Same factor for R, G and B of a pixel → hue and saturation unchanged.
        # Shoulder: mid-tones get ~the full gain, highlights approach white
        # smoothly instead of being clipped (shading on white paint survives).
        gain = np.float32(2.0 ** correction.exposure_ev)
        y = np.maximum(luminance(out), 1e-6)
        target = gain * y / (1.0 + (gain - 1.0) * np.minimum(y, 1.0))
        out = out * (target / y)[..., None]
    if correction.contrast:
        pivot = np.float32(NEUTRAL_KEY)
        out = pivot * np.power(np.maximum(out, 0) / pivot, 1.0 + correction.contrast)
    # Never clip single channels (that would shift the hue): scale the pixel down.
    peak = out.max(axis=-1, keepdims=True)
    out = np.where(peak > 1.0, out / np.maximum(peak, 1e-6), out)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def colour_shift(before: np.ndarray, after: np.ndarray) -> tuple[float, float, float]:
    """Hue shift (95th percentile, deg) of chromatic pixels, relative saturation
    change and the fraction of pixels that newly reach full white."""
    if len(before) == 0:
        return 0.0, 0.0, 0.0
    lab_b = lab_from_linear(before.reshape(-1, 1, 3)).reshape(-1, 3)
    lab_a = lab_from_linear(after.reshape(-1, 1, 3)).reshape(-1, 3)
    chromatic = np.hypot(lab_b[:, 1], lab_b[:, 2]) > 8.0
    if chromatic.sum() > 50:
        dh = hue_degrees(lab_a[chromatic]) - hue_degrees(lab_b[chromatic])
        dh = (dh + 180.0) % 360.0 - 180.0
        hue_shift = float(np.percentile(np.abs(dh), 95))
    else:
        hue_shift = 0.0
    sat_b = float(np.mean(linear_saturation(before)))
    sat_a = float(np.mean(linear_saturation(after)))
    sat_change = (sat_a - sat_b) / max(sat_b, 1e-4)
    white = 0.995
    newly_clipped = (after.max(axis=-1) >= white) & (before.max(axis=-1) < white)
    return hue_shift, sat_change, float(newly_clipped.mean())


def guarded_correction(
    vehicle_linear: np.ndarray, vehicle_alpha: np.ndarray, correction: VehicleCorrection
) -> tuple[np.ndarray, VehicleCorrection]:
    """Apply the correction only if the paint colour stays the same."""
    mask = vehicle_alpha > 0.9
    sample_idx = np.flatnonzero(mask.ravel())
    if len(sample_idx) > 200_000:
        sample_idx = sample_idx[:: int(np.ceil(len(sample_idx) / 200_000))]
    before = vehicle_linear.reshape(-1, 3)[sample_idx]

    attempts = [
        ("none", correction),
        ("white_balance", VehicleCorrection(exposure_ev=correction.exposure_ev, contrast=correction.contrast)),
        ("exposure", VehicleCorrection(wb_gains=correction.wb_gains, contrast=correction.contrast)),
        ("all", VehicleCorrection()),
    ]
    for reverted, candidate in attempts:
        after = apply_correction(before, candidate)
        hue_shift, sat_change, clipped = colour_shift(before, after)
        candidate.hue_shift_p95_deg = hue_shift
        candidate.saturation_change = sat_change
        candidate.new_clipped_fraction = clipped
        ok = (
            hue_shift <= MAX_HUE_SHIFT_P95_DEG
            and abs(sat_change) <= MAX_SATURATION_CHANGE
            and clipped <= MAX_NEW_CLIPPED_FRACTION
        )
        if ok or reverted == "all":
            candidate.guard_passed = ok
            candidate.reverted = None if reverted == "none" else reverted
            if reverted == "all":
                return vehicle_linear, candidate
            return apply_correction(vehicle_linear, candidate), candidate
    return vehicle_linear, VehicleCorrection()  # pragma: no cover
