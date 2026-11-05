"""STEP 9 – light matching for the VEHICLE layer.

Two modes: the conservative correction below (shipped preset) and – the dealer's
decision, preset ``vehicleAdjustments.studioLight`` – the guarded global studio
light match further down (wider, documented limits; paint guard).

Conservative correction:

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

import cv2
import numpy as np

from ..presets import STUDIO_LIGHT_RANGES, StudioLightConfig, VehicleAdjustments
from .color import hue_degrees, lab_from_linear, linear_saturation, linear_to_srgb, luminance, srgb_to_linear

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


# ════════════════════════════════════════════════════════════════════════════
# Studio light match (vehicleAdjustments.studioLight) – the dealer's decision
# ════════════════════════════════════════════════════════════════════════════
#
# The owner of the project decided (2026-11-05) that the car may get a Lightroom-like GLOBAL
# light correction so it matches the bright studio plate: exposure, a contrast S-curve with a
# highlight ceiling, an optional shadow lift and a white balance towards the studio's light –
# beyond the conservative limits above. What stays: no generative AI, no relighting model,
# nothing drawn, removed or retouched; every operation is ONE global mapping of the original
# pixel values (the same function for every pixel of the vehicle layer, no spatial filter),
# ratio-preserving for the tone part (every pixel keeps its R:G:B ratio – hue and linear
# saturation – exactly; only the white balance changes ratios). The paint guard measures the
# paint (large uniform body areas) before/after and scales the correction back until hue,
# saturation, tone class and shading of the paint, the car's blacks and highlights stay within
# the limits.

#: Absolute maxima of the studio light match (presets.py validates the same ranges).
STUDIO_HARD_LIMITS = STUDIO_LIGHT_RANGES

#: The studio's light is measured on the plate around the car: the brand wall in this share of
#: the plate height above the car's top (white level and neutral colour of the light) and the
#: plate behind the car's box (key light: median luminance of wall and floor there).
STUDIO_WALL_BAND = 0.12
#: Contrast fades out below 2^-4.5 (~4 %) of the car's median luminance (deep blacks are not
#: crushed) and is complete from 2^-1.5 (35 %) of it.
CONTRAST_TOE = (-4.5, -1.5)
#: The exposure lift fades in between these luminances (linear; L* ≈ 3 … 20): the deepest blacks of
#: the car keep their level (no grey tyres, no grey black trim, black paint keeps its depth).
BLACK_ANCHOR = (0.003, 0.03)
#: Vehicle black guard: the car's darkest 2 % may rise at most this much (L*).
MAX_VEHICLE_BLACK_LIFT = 2.0
#: The shadow lift acts between 2^-5 of the median and the median (peak at 2^-2.5 ≈ 1/6).
SHADOW_RANGE = (-5.0, 0.0)
#: Paint shading guard: the paint's lightness spread (L* 10th … 90th percentile) keeps this share.
MIN_PAINT_SHADING = 0.9
#: Highlight guard (a second line of defence behind the highlight ceiling): the detail (std of the
#: brightest channel, sRGB) of the vehicle's bright pixels (>= 0.6 linear before) keeps this share.
MIN_HIGHLIGHT_DETAIL = 0.55
#: Paint finder: working size and smoothness (local CIELAB standard deviation) of "uniform".
PAINT_WORK_EDGE = 640
PAINT_SMOOTH_STD = 3.0
#: Paint with a mean chroma below this is near-neutral (white, silver, grey, black): its hue is
#: not defined, its chroma change is limited in absolute CIELAB units instead.
NEUTRAL_PAINT_CHROMA = 10.0
#: Tone class of the paint: the lift allowance ramps from max_black_lift (median L* <= 25) to
#: max_paint_lightness_shift (median L* >= 45).
DARK_PAINT_L = (25.0, 45.0)
#: Guard search: bisection steps of the correction strength.
GUARD_STEPS = 10
#: The white balance may use this share of the hue / saturation / chroma limits; the rest is
#: left for the (ratio-preserving, but not perfectly CIELAB-neutral) tone curve.
WB_COLOUR_BUDGET = 0.75


@dataclass
class StudioLight:
    """The studio's light near the car, measured on the (branded) plate."""

    #: 95th percentile luminance of the brand wall above the car (linear).
    white: float
    #: Median luminance of the plate behind/around the car (wall + floor, linear).
    key: float
    #: Colour of the studio's white light (mean of the bright, low-chroma wall), luminance 1.
    neutral: tuple[float, float, float]

    def as_dict(self) -> dict:
        return {"white": round(self.white, 4), "key": round(self.key, 4), "neutral": [round(v, 4) for v in self.neutral]}


@dataclass
class StudioPlan:
    """The planned (full-strength) global mapping; the guard's scales (0..1) shrink exposure,
    curve shape and white balance separately."""

    exposure_ev: float = 0.0
    contrast: float = 0.0
    shadows: float = 0.0
    wb_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    #: Pivot of the contrast/shadow curve: the car's median luminance (linear).
    pivot: float = 0.18
    highlight_slope: float = 0.8
    white_limit: float = 0.985


def _luma_normalised(rgb: np.ndarray) -> np.ndarray:
    return rgb / max(float(np.dot(rgb, [0.2126, 0.7152, 0.0722])), 1e-6)


def measure_studio(showroom_rgb: np.ndarray, box: tuple[float, float, float, float]) -> StudioLight:
    """White level, key light and neutral colour of the studio around the car's box
    (left, top, width, height in plate px)."""
    srgb = np.asarray(showroom_rgb)
    height, width = srgb.shape[:2]
    left, top, w, h = box
    x0 = int(np.clip(left, 0, width - 1))
    x1 = int(np.clip(left + w, x0 + 1, width))
    step = max(1, int(max(height, width) / 1200))
    band = (int(np.clip(top - STUDIO_WALL_BAND * height, 0, height - 1)), int(np.clip(top, 1, height)))
    wall = srgb[band[0] : max(band[1], band[0] + 1) : step, x0:x1:step]
    if wall.size < 300:  # car touches the top: the upper half of the plate
        wall = srgb[: height // 2 : step, ::step]
    wall_lin = srgb_to_linear(wall).reshape(-1, 3)
    wall_y = luminance(wall_lin)
    white = float(np.percentile(wall_y, 95))
    lo, hi = np.percentile(wall_y, [30, 90])
    sat = linear_saturation(wall_lin)
    pick = (wall_y >= lo) & (wall_y <= hi) & (sat < 0.25)
    neutral = _luma_normalised(wall_lin[pick].mean(axis=0)) if pick.sum() > 50 else np.ones(3)
    margin = 0.05 * w
    around = srgb[
        int(np.clip(top - 0.1 * height, 0, height - 1)) : int(np.clip(top + h, 1, height)) : step,
        int(np.clip(left - margin, 0, width - 1)) : int(np.clip(left + w + margin, 1, width)) : step,
    ]
    key = float(np.median(luminance(srgb_to_linear(around)))) if around.size else white
    return StudioLight(white=white, key=key, neutral=tuple(float(v) for v in neutral))


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def tone_curve(y: np.ndarray, plan: StudioPlan, scale_ev: float = 1.0, scale_shape: float = 1.0) -> np.ndarray:
    """Luminance → luminance: exposure, contrast S-curve around the car's median (fading out in
    the deep shadows) and shadow lift, in log2 space. Monotone for the allowed parameters."""
    pivot = max(plan.pivot, 1e-4)
    x = np.log2(np.maximum(y, 1e-6) / pivot)
    contrast = plan.contrast * scale_shape
    shadows = plan.shadows * scale_shape
    f = x + contrast * x * _smoothstep(*CONTRAST_TOE, x)
    if shadows:
        u = np.clip((x - SHADOW_RANGE[0]) / (SHADOW_RANGE[1] - SHADOW_RANGE[0]), 0.0, 1.0)
        f = f + shadows * 4.0 * u * (1.0 - u)
    # the black point stays: the exposure lift fades out in the deepest blacks (tyres, trim in shadow)
    lift = plan.exposure_ev * scale_ev * _smoothstep(BLACK_ANCHOR[0], BLACK_ANCHOR[1], np.maximum(y, 0.0))
    return (pivot * np.exp2(lift + f)).astype(np.float32)


def apply_studio_light(
    linear: np.ndarray, plan: StudioPlan, scale_ev: float = 1.0, scale_shape: float = 1.0, scale_wb: float = 1.0
) -> np.ndarray:
    """The global mapping (any shape (…, 3), linear light). Same function for every pixel.
    ``scale_*`` shrink the exposure, the curve shape (contrast + shadows) and the white balance."""
    gains = 1.0 + scale_wb * (np.asarray(plan.wb_gains, np.float32) - 1.0)
    rgb = (linear * gains).astype(np.float32)
    y = np.maximum(luminance(rgb), 1e-6)
    out = rgb * (tone_curve(y, plan, scale_ev, scale_shape) / y)[..., None]
    # highlights (brightest channel, the pixel's ratios kept): a lifted pixel rises at most to the
    # highlight ceiling – it keeps ``highlight_slope`` of its distance to ``white_limit`` – so
    # chrome, headlights and white paint keep their detail instead of being pushed into a flat
    # white, nothing new clips, and nothing ends darker than it was (min of two monotone maps)
    peak = out.max(axis=-1)
    original = linear.max(axis=-1)  # as photographed (before the white balance)
    white = plan.white_limit
    ceiling = np.maximum(original, white - (white - original) * plan.highlight_slope)
    target = np.minimum(peak, ceiling)
    out = out * (target / np.maximum(peak, 1e-6))[..., None]
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def find_paint(vehicle_linear: np.ndarray, vehicle_alpha: np.ndarray) -> tuple[np.ndarray, dict]:
    """Pixels (N×3, linear) of the paint: the large uniform body areas.

    On a downscaled copy of the layer: smooth pixels (local CIELAB std < PAINT_SMOOTH_STD) of
    the opaque core; if at least 30 % of them are chromatic (C* >= 8) the paint is the dominant
    hue (chroma-weighted histogram, ±25°), else the dominant lightness of the neutral smooth
    pixels in the upper 80 % of the car (tyres excluded), ±15 L*."""
    ys, xs = np.nonzero(vehicle_alpha > 0.5)
    if len(ys) < 100:
        return np.zeros((0, 3), np.float32), {"kind": "none"}
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop, alpha = vehicle_linear[y0:y1, x0:x1], vehicle_alpha[y0:y1, x0:x1]
    s = min(1.0, PAINT_WORK_EDGE / max(crop.shape[:2]))
    size = (max(1, round(crop.shape[1] * s)), max(1, round(crop.shape[0] * s)))
    small = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, size, interpolation=cv2.INTER_AREA)
    core = cv2.erode((alpha > 0.97).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    lab = lab_from_linear(small)
    mean = [cv2.blur(lab[..., i], (7, 7)) for i in range(3)]
    var = sum(cv2.blur(lab[..., i] ** 2, (7, 7)) - mean[i] ** 2 for i in range(3))
    smooth = core & (np.sqrt(np.maximum(var, 0.0)) < PAINT_SMOOTH_STD) & (lab[..., 0] > 3.0)
    n = int(smooth.sum())
    if n < 200:
        return np.zeros((0, 3), np.float32), {"kind": "none"}
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = hue_degrees(lab)
    chromatic = smooth & (chroma >= 8.0)
    if chromatic.sum() >= 0.3 * n:
        hist = np.histogram(hue[chromatic], bins=72, range=(-180, 180), weights=chroma[chromatic])[0]
        hist = np.convolve(np.concatenate([hist[-2:], hist, hist[:2]]), np.ones(5) / 5, "valid")
        mode = -180.0 + 5.0 * (int(np.argmax(hist)) + 0.5)
        paint = smooth & (chroma >= 5.0) & (np.abs((hue - mode + 180.0) % 360.0 - 180.0) < 25.0)
        # the body, not tinted glass of the same hue: the dominant lightness ±25 L*
        hist = np.histogram(lab[..., 0][paint], bins=20, range=(0, 100))[0].astype(float)
        l_mode = 5.0 * int(np.argmax(np.convolve(hist, [0.25, 0.5, 0.25], "same"))) + 2.5
        paint &= np.abs(lab[..., 0] - l_mode) < 25.0
        kind = "chromatic"
    else:
        neutral = smooth & (chroma < 8.0)
        upper = (np.arange(neutral.shape[0]) < 0.8 * neutral.shape[0])[:, None]
        hist = np.histogram(lab[..., 0][neutral & upper], bins=20, range=(0, 100))[0].astype(float)
        mode = 5.0 * int(np.argmax(np.convolve(hist, [0.25, 0.5, 0.25], "same"))) + 2.5
        paint = neutral & (np.abs(lab[..., 0] - mode) < 15.0)
        kind = "neutral"
    return small[paint].astype(np.float32), {"kind": kind, "share": round(float(paint.sum() / max(core.sum(), 1)), 3)}


def paint_stats(pixels: np.ndarray) -> dict:
    """Colour of the paint pixels: CIELAB of their mean colour, per-pixel lightness percentiles
    and (key ``lab``) the per-pixel CIELAB values for the per-pixel hue / saturation guard."""
    lab = lab_from_linear(pixels.reshape(-1, 1, 3)).reshape(-1, 3)
    mean = lab_from_linear(pixels.mean(axis=0).reshape(1, 1, 3)).reshape(3)
    return {
        "L": float(mean[0]),
        "C": float(np.hypot(mean[1], mean[2])),
        "hue": float(np.degrees(np.arctan2(mean[2], mean[1]))),
        "L10": float(np.percentile(lab[:, 0], 10)),
        "L50": float(np.percentile(lab[:, 0], 50)),
        "L90": float(np.percentile(lab[:, 0], 90)),
        "lab": lab,
    }


def lift_allowance(l50: float, cfg: StudioLightConfig) -> float:
    """How much the paint's median L* may move (tone class): max_black_lift for black / very dark
    paint, max_paint_lightness_shift from mid tones on, linear in between."""
    black = min(cfg.max_black_lift, STUDIO_HARD_LIMITS["max_black_lift"][1])
    other = min(cfg.max_paint_lightness_shift, STUDIO_HARD_LIMITS["max_paint_lightness_shift"][1])
    t = float(np.clip((l50 - DARK_PAINT_L[0]) / (DARK_PAINT_L[1] - DARK_PAINT_L[0]), 0.0, 1.0))
    return black + t * (max(other, black) - black)


def paint_guard(
    before: dict,
    after: dict,
    vehicle_before: np.ndarray,
    vehicle_after: np.ndarray,
    cfg: StudioLightConfig,
    colour_budget: float = 1.0,
) -> tuple[list[str], dict]:
    """Failed guard names + the measured changes (``colour_budget`` scales the hue, saturation
    and neutral-chroma limits)."""
    hard = STUDIO_HARD_LIMITS
    failed: list[str] = []
    m: dict = {}
    if before:
        neutral = before["C"] < NEUTRAL_PAINT_CHROMA
        lab_b, lab_a = before["lab"], after["lab"]
        chroma_b = np.hypot(lab_b[:, 1], lab_b[:, 2])
        # per pixel: the tone curve keeps every pixel's R:G:B ratio, so only a real colour change
        # (white balance) moves these – not a different mix of bright and dark paint pixels
        chromatic = (chroma_b >= 8.0) & (lab_b[:, 0] >= 5.0)
        if not neutral and chromatic.sum() >= 50:
            dh = (hue_degrees(lab_a[chromatic]) - hue_degrees(lab_b[chromatic]) + 180.0) % 360.0 - 180.0
            # saturation C*/(L*+16): exactly invariant to an exposure gain (cube-root range of
            # CIELAB), so a brighter car does not count as a more or less saturated one
            sat_b = chroma_b[chromatic] / (lab_b[chromatic, 0] + 16.0)
            sat_a = np.hypot(lab_a[chromatic, 1], lab_a[chromatic, 2]) / (lab_a[chromatic, 0] + 16.0)
            m["hueShiftDeg"] = float(np.median(np.abs(dh)))
            m["hueShiftP90Deg"] = float(np.percentile(np.abs(dh), 90))
            m["saturationChange"] = float(np.median(sat_a / sat_b) - 1.0)
        else:
            m["hueShiftDeg"] = m["hueShiftP90Deg"] = 0.0
            m["saturationChange"] = 0.0
        m["chromaShift"] = after["C"] - before["C"]
        m["lightnessShift"] = after["L50"] - before["L50"]
        m["blackPointShift"] = after["L10"] - before["L10"]
        budget = colour_budget
        max_hue = budget * min(cfg.max_hue_shift_deg, hard["max_hue_shift_deg"][1])
        # the paint's hue (median) and no larger part of it (90th percentile, 2× the limit)
        if not neutral and (m["hueShiftDeg"] > max_hue or m["hueShiftP90Deg"] > 2.0 * max_hue):
            failed.append("hue")
        if neutral:
            limit = budget * min(cfg.max_neutral_chroma_shift, hard["max_neutral_chroma_shift"][1])
            if abs(m["chromaShift"]) > limit:
                failed.append("neutralChroma")
        elif abs(m["saturationChange"]) > budget * min(cfg.max_saturation_change, hard["max_saturation_change"][1]):
            failed.append("saturation")
        allowance = lift_allowance(before["L50"], cfg)
        m["lightnessAllowance"] = allowance
        if abs(m["lightnessShift"]) > allowance:
            failed.append("toneClass")
        if before["L50"] < DARK_PAINT_L[1] and m["blackPointShift"] > allowance:
            failed.append("blackPoint")
        # the paint keeps its shading (a saturated paint lifted against the highlight ceiling
        # would otherwise flatten): spread of its lightness, 10th to 90th percentile
        spread = before["L90"] - before["L10"]
        if spread > 5.0:
            m["paintShading"] = (after["L90"] - after["L10"]) / spread
            if m["paintShading"] < MIN_PAINT_SHADING:
                failed.append("paintShading")
    white = 0.995
    peak_b, peak_a = vehicle_before.max(axis=-1), vehicle_after.max(axis=-1)
    clipped = (peak_a >= white) & (peak_b < white)
    m["newClippedFraction"] = float(clipped.mean()) if len(clipped) else 0.0
    if m["newClippedFraction"] > min(cfg.max_new_clipped, hard["max_new_clipped"][1]):
        failed.append("clipping")
    if len(vehicle_before) >= 200:
        l_b = lab_from_linear(vehicle_before.reshape(-1, 1, 3)).reshape(-1, 3)[:, 0]
        l_a = lab_from_linear(vehicle_after.reshape(-1, 1, 3)).reshape(-1, 3)[:, 0]
        m["vehicleBlackShift"] = float(np.percentile(l_a, 2) - np.percentile(l_b, 2))
        if m["vehicleBlackShift"] > MAX_VEHICLE_BLACK_LIFT:
            failed.append("blacks")
    bright = peak_b >= 0.6
    if bright.sum() >= 200:
        detail_b = float(np.std(linear_to_srgb(peak_b[bright])))
        detail_a = float(np.std(linear_to_srgb(peak_a[bright])))
        m["highlightDetail"] = detail_a / max(detail_b, 1e-4)
        if m["highlightDetail"] < MIN_HIGHLIGHT_DETAIL:
            failed.append("highlights")
    return failed, m


def plan_studio_light(
    vehicle_linear: np.ndarray, vehicle_alpha: np.ndarray, studio: StudioLight, cfg: StudioLightConfig
) -> tuple[StudioPlan, dict]:
    """Full-strength plan from the ORIGINAL vehicle pixels and the studio's light (measurement)."""
    hard = STUDIO_HARD_LIMITS
    core = vehicle_alpha > 0.97
    sample = _sample(vehicle_linear[core]) if core.any() else vehicle_linear.reshape(-1, 3)
    y = luminance(sample)
    median = float(np.median(y)) if len(y) else 0.18
    measured = {
        "carMedian": median,
        "carP95": float(np.percentile(y, 95)) if len(y) else 0.0,
        "carP99": float(np.percentile(y, 99)) if len(y) else 0.0,
    }
    # exposure: the car's median luminance towards key_target × the studio's key (log2 space,
    # ``exposure_strength`` of the difference); never darker, at most max_exposure_ev
    strength = min(cfg.exposure_strength, hard["exposure_strength"][1])
    target = min(cfg.key_target, hard["key_target"][1]) * studio.key
    raw_ev = float(np.log2(max(target, 1e-4) / max(median, 1e-4)))
    ev = float(np.clip(strength * raw_ev, 0.0, min(cfg.max_exposure_ev, hard["max_exposure_ev"][1])))
    # white balance: the car's neutral parts (tyres, glass, chrome, neutral paint) towards the
    # colour of the studio's white light, ``white_balance_strength`` of it, luminance kept
    gains = (1.0, 1.0, 1.0)
    lab = lab_from_linear(sample.reshape(-1, 1, 3)).reshape(-1, 3)
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    neutral = (chroma < 6.0) & (lab[:, 0] > 15.0) & (lab[:, 0] < 90.0)
    limit = min(cfg.max_white_balance, hard["max_white_balance"][1])
    if limit > 0 and neutral.sum() > max(200, 0.02 * len(sample)):
        car_neutral = _luma_normalised(sample[neutral].mean(axis=0))
        measured["carNeutral"] = [round(float(v), 4) for v in car_neutral]
        raw = _luma_normalised(np.asarray(studio.neutral) / np.maximum(car_neutral, 1e-5))
        deviation = min(cfg.white_balance_strength, hard["white_balance_strength"][1]) * (raw - 1.0)
        largest = float(np.max(np.abs(deviation)))
        if largest > limit:
            deviation *= limit / largest  # keeps the luminance (Σ w·dev = 0)
        gains = tuple(float(1.0 + d) for d in deviation)
    plan = StudioPlan(
        exposure_ev=ev,
        contrast=float(min(cfg.contrast, hard["contrast"][1])),
        shadows=float(min(cfg.shadows, hard["shadows"][1])),
        wb_gains=gains,
        pivot=max(median, 1e-4),
        highlight_slope=float(np.clip(cfg.highlight_slope, *hard["highlight_slope"])),
        white_limit=float(np.clip(cfg.white_limit, *hard["white_limit"])),
    )
    measured["rawExposureEv"] = raw_ev
    measured["targetMedian"] = target
    return plan, measured


def studio_light_correction(
    vehicle_linear: np.ndarray, vehicle_alpha: np.ndarray, studio: StudioLight, cfg: StudioLightConfig
) -> tuple[np.ndarray, dict]:
    """Plan, guard (scale back until the paint stays within its limits), apply. Returns the
    corrected layer and the metadata of what was measured and applied."""
    plan, measured = plan_studio_light(vehicle_linear, vehicle_alpha, studio, cfg)
    paint, paint_info = find_paint(vehicle_linear, vehicle_alpha)
    paint = _sample(paint, 60_000)
    mask = vehicle_alpha > 0.9
    vehicle = _sample(vehicle_linear[mask], 200_000) if mask.any() else vehicle_linear.reshape(-1, 3)[:0]
    before = paint_stats(paint) if len(paint) >= 100 else {}

    def check(ev: float, shape: float, wb: float, budget: float = 1.0) -> tuple[list[str], dict, dict]:
        after = paint_stats(apply_studio_light(paint, plan, ev, shape, wb)) if before else {}
        vehicle_after = apply_studio_light(vehicle, plan, ev, shape, wb)
        failed, metrics = paint_guard(before, after, vehicle, vehicle_after, cfg, colour_budget=budget)
        return failed, metrics, after

    def largest(passes) -> float:
        if passes(1.0):
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(GUARD_STEPS):
            mid = 0.5 * (low + high)
            low, high = (mid, high) if passes(mid) else (low, mid)
        return low

    colour_guards = {"hue", "saturation", "neutralChroma"}
    limited: dict[str, list[str]] = {}
    # 1) white balance alone: hue / saturation of the paint
    failed = check(0.0, 0.0, 1.0, WB_COLOUR_BUDGET)[0]
    wb_fails = set(failed) & colour_guards
    scale_wb = largest(lambda w: not (set(check(0.0, 0.0, w, WB_COLOUR_BUDGET)[0]) & colour_guards)) if wb_fails else 1.0
    if scale_wb < 1.0:
        limited["whiteBalance"] = sorted(wb_fails)
    # 2) curve shape (contrast, shadow lift) with that white balance
    failed = check(0.0, 1.0, scale_wb)[0]
    scale_shape = largest(lambda t: not check(0.0, t, scale_wb)[0]) if failed else 1.0
    if scale_shape < 1.0:
        limited["shape"] = failed
    # 3) exposure on top: as far as the paint's tone class, saturation and highlights allow
    failed = check(1.0, scale_shape, scale_wb)[0]
    scale_ev = largest(lambda t: not check(t, scale_shape, scale_wb)[0]) if failed else 1.0
    if scale_ev < 1.0:
        limited["exposure"] = failed
    failed, metrics, after = check(scale_ev, scale_shape, scale_wb)
    if failed:  # even the white balance alone fails (e.g. clipping) – no correction at all
        scale_ev = scale_shape = scale_wb = 0.0
        failed, metrics, after = check(0.0, 0.0, 0.0)
    corrected = apply_studio_light(vehicle_linear, plan, scale_ev, scale_shape, scale_wb)
    gains = 1.0 + scale_wb * (np.asarray(plan.wb_gains) - 1.0)

    def rounded(d: dict) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items() if k != "lab"}

    info = {
        "mode": "studioLight",
        "studio": studio.as_dict(),
        "measured": rounded(measured),
        "planned": {
            "exposureEv": round(plan.exposure_ev, 4),
            "contrast": round(plan.contrast, 4),
            "shadows": round(plan.shadows, 4),
            "wbGains": [round(g, 4) for g in plan.wb_gains],
            "pivot": round(plan.pivot, 4),
            "highlightSlope": plan.highlight_slope,
            "whiteLimit": plan.white_limit,
        },
        "applied": {
            "exposureScale": round(scale_ev, 4),
            "shapeScale": round(scale_shape, 4),
            "wbScale": round(scale_wb, 4),
            "exposureEv": round(plan.exposure_ev * scale_ev, 4),
            "contrast": round(plan.contrast * scale_shape, 4),
            "shadows": round(plan.shadows * scale_shape, 4),
            "wbGains": [round(float(g), 4) for g in gains],
        },
        "paint": {**paint_info, "before": rounded(before), "after": rounded(after)},
        "guard": {**rounded(metrics), "limitedBy": limited, "passed": not failed},
    }
    return corrected, info
