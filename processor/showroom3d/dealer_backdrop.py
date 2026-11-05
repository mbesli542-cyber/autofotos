"""Step 4 of the dealer backdrop: assemble the plate set the processor loads.

Input
    --plate    the cleaned, upscaled dealer image (dealer_clean.py → dealer_upscale.py), 2400 × 1800
    --render   the Blender passes (render_dealer_passes.py): base.png, <shot>-proxy.png, layout.json
    --measure  dealer_clean.py's measure.json (where his lettering was)
Output (``public/presets/autoexperten-haendler/``)
    plate.jpg              THE plate of all eight exterior shots (one fixed studio camera; sRGB
                           JPEG, embedded sRGB profile, no text or logo)
    <shot>-shadow.png      16-bit floor shadow/occlusion multiplier of the proxy car at the shot's
                           pose (LINEAR, 65535 = no shadow) – the ratio of the two Blender renders,
                           limited to the floor around the car (postprocess_plates.shadow_multiplier)
    reflection.png         16-bit RGB at the plate's full size, the floor's OWN glossy reflection
                           (light streaks of the wall washers, LED lines, wall glow) in LINEAR light
                           (code / 65535 − reflectionOffset, offset 0: never negative); one file –
                           the camera never moves
    plates.json            camera, horizon, floor/wall homographies, per shot the proxy car pose,
                           bbox and tyre contacts, wall albedo, brand area, reflectance per row
    branding.json          the official branding layout in wall space that reproduces the position
                           and size of his lettering (copied into the preset's ``branding``)

The reflection pass is MEASURED on his floor, not rendered: his image was made with an image AI,
so a rendered gloss (any roughness) never matches the streaks he drew. In linear light every
floor pixel (low-passed, the wood grain averaged out) is split into three known colours –
the wood (the most saturated floor pixels), the warm white light of the wall washers (the
pools on his wall) and the AutoExperten-blue LEDs (his corner LED) – so that
``floor = wood · D + light · R + blue · B``. ``light · R + blue · B`` is the floor's own
reflection (stored as that smooth per-channel fraction of the plate's own pixels, so it follows
the sharp streak cores); what stays (``wood · D``) is the matte wood the processor shows where a
car blocks that reflection. The pass is stored at the plate's full size and limited per pixel
(``limit_saturation``): what is left after removing it is never more saturated than the floor's
own wood – the plank seams inside a streak carry no gloss (they show the bare wood), so removing
the streak's share there would turn them into orange-red lines. In the hot streak cores the split
leaves too little wood under the near-white light (removed under a car: dark grey bands), so the wood
left in a core is lifted to the unlit wood next to it (``lift_streak_cores``). ``reflectance`` per image row is
calibrated exactly like the rendered plates (postprocess_plates.reflection_pass: reflection of
the white wall / wall radiance at the mirrored row – the frontal level camera mirrors the wall
about the junction row).

Usage (from the repository root, processor virtualenv)::

    processor/.venv/bin/python processor/showroom3d/dealer_backdrop.py \
        --plate /opt/ae-dealer/up/lanczos.png --render /opt/ae-dealer/render \
        --measure /opt/ae-dealer/clean/measure.json --out public/presets/autoexperten-haendler \
        --standard public/presets/autoexperten-standard.json \
        --variant public/presets/variants/autoexperten-haendler.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dealer_layout as dl  # noqa: E402
import postprocess_plates as pp  # noqa: E402

PLATE_NAME = "plate.jpg"
REFLECTION_NAME = "reflection.png"
PASS_SIZE = (1200, 900)
#: Low-pass (px at PASS_SIZE) before the colour split: averages the wood grain, keeps the streaks.
GRAIN_SIGMA = 2.5
#: Floor pixels whose chroma is among the most saturated this fraction define the wood colour.
WOOD_QUANTILE = 0.08
#: Columns (normalised) of the corner LED (blue reference) and the pool rows of the wall (light reference).
LED_COLUMNS = (0.792, 0.800)
LIGHT_BOX = (0.15, 0.0, 0.75, 0.06)  # u0, v0, u1, v1 of the pool band on the wall
#: Wood under the streak cores: at least this fraction of the surrounding floor's wood (Gaussian σ px).
DIFFUSE_FLOOR, DIFFUSE_SIGMA = 0.9, (30.0, 10.0)
#: The reflection fraction (reflection / plate, per channel) is smoothed by this σ (px at PASS_SIZE).
FRACTION_SIGMA = 2.0
#: Encoding offset of reflection.png (code / 65535 − offset). The measured reflection is never
#: negative but reaches ≈ 0.97 in the hottest streak cores – the rendered plates' 0.5 (room for the
#: negative channel mixing of AgX) would clip it there and leave yellow remains under a car.
REFLECTION_OFFSET = 0.0
#: reflection.png code step (of 65535): ≈ 10-bit precision (≤ 0.0005 linear, < 0.3 sRGB levels on the
#: floor) – keeps the full-size 16-bit pass at ≈ 5 MB instead of ≈ 11 MB.
REFLECTION_STEP = 64
#: The reflection is kept this many px (PASS_SIZE) below the actual floor line (skirting, LEDs, planter).
FLOOR_PAD = 3
#: Saturation guard of the stored (full-size) pass: the wood left after removing the reflection keeps
#: b/r and g/r at least at this percentile of the floor's own reflection-poor pixels.
SATURATION_PERCENTILE = 1.0
#: Floor pixels whose reflection is below this fraction of the plate define that natural wood.
WOOD_ONLY_FRACTION = 0.08
#: Streak cores: the wood left after removing the pass is lifted to the unlit wood next to them
#: (lift_streak_cores, all at PASS_SIZE). The row's baseline share of reflection is the lowest
#: share within ±BASE_HALF px; pixels within UNLIT_BAND of it are its unlit wood (averaged with
#: a Gaussian of UNLIT_SIGMA px, wider where no such pixel is near); pixels more than CORE_OFFSET
#: above it are lifted, fully from CORE_OFFSET + CORE_SPAN on; the gain is at most MAX_LIFT.
BASE_HALF = 80
UNLIT_BAND = 0.06
UNLIT_SIGMA = (40.0, 6.0)
CORE_OFFSET, CORE_SPAN = 0.05, 0.10
MAX_LIFT = 1.8


LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def floor_region(width: int, height: int, room: dict) -> np.ndarray:
    """Pixels that really show the floor: below the main junction, below the right wall's floor
    line, not the planter."""
    v = (np.arange(height) + 0.5)[:, None] / height
    u = (np.arange(width) + 0.5)[None, :] / width
    floor = np.broadcast_to(v > dl.JUNCTION_V, (height, width)).copy()
    # right wall: its floor line seen per column (through the corner along its direction)
    corner = np.array(room["rightWall"]["corner"])
    direction = np.array(room["rightWall"]["direction"])
    cu = dl.project(np.array([corner[0], corner[1], 0.0]))[0]
    for col in range(width):
        if u[0, col] <= cu:
            continue
        p = dl._solve_column(corner, direction, float(u[0, col]))
        _, pv, _ = dl.project(np.array([p[0], p[1], 0.0]))
        floor[: int(np.ceil(float(pv) * height)) + FLOOR_PAD, col] = False
    first = int(np.ceil(dl.JUNCTION_V * height)) + FLOOR_PAD
    floor[:first] = False
    # planter: its front face down to its bottom edge
    pl = room["planter"]
    pu, pv, _ = dl.project(np.array([pl["rightX"], pl["frontY"], 0.0]))
    floor[: int(np.ceil(float(pv) * height)) + FLOOR_PAD, : int(np.ceil(float(pu) * width)) + FLOOR_PAD] = False
    return floor


def plate_linear(plate_rgb: np.ndarray) -> np.ndarray:
    """The plate in linear light at PASS_SIZE (linearised first, then area-averaged – exactly as
    postprocess_plates.reflection_pass reads the plate)."""
    lin = _to_linear(plate_rgb.astype(np.float32) / 255.0).astype(np.float32)
    return cv2.resize(lin, PASS_SIZE, interpolation=cv2.INTER_AREA)


def split_reflection(plate_rgb: np.ndarray, room: dict, *, parts: bool = False):
    """The floor's own reflection (linear RGB at PASS_SIZE) and the colours used
    (``parts``: also the smooth per-channel fraction and the soft floor weight, both at PASS_SIZE)."""
    width, height = PASS_SIZE
    lin = plate_linear(plate_rgb)
    low = cv2.GaussianBlur(lin, (0, 0), GRAIN_SIGMA)
    floor = floor_region(width, height, room)
    # wood: the most saturated floor colour (lowest blue/red ratio)
    pix = low[floor]
    ratio = pix[:, 2] / np.maximum(pix[:, 0], 1e-6)
    wood = pix[ratio <= np.quantile(ratio, WOOD_QUANTILE)].mean(axis=0)
    # light: the wall-washer pools on the wall; blue: the corner LED strip
    u0, v0, u1, v1 = LIGHT_BOX
    pool = lin[int(v0 * height) : int(v1 * height) + 1, int(u0 * width) : int(u1 * width)].reshape(-1, 3)
    pool = pool[pool.sum(axis=1) >= np.quantile(pool.sum(axis=1), 0.9)]
    light = pool.mean(axis=0)
    led = lin[int(0.1 * height) : int(0.4 * height), int(LED_COLUMNS[0] * width) : int(LED_COLUMNS[1] * width) + 1].reshape(-1, 3)
    led = led[(led[:, 2] - led[:, 0]) >= np.quantile(led[:, 2] - led[:, 0], 0.9)]
    blue = led.mean(axis=0)
    basis = np.stack([wood / np.linalg.norm(wood), light / np.linalg.norm(light), blue / np.linalg.norm(blue)], axis=1)
    coeff = low.reshape(-1, 3) @ np.linalg.inv(basis).T  # (D, R, B) per pixel
    diffuse = np.clip(coeff.reshape(height, width, 3)[..., 0], 0.0, None)
    # in the hottest streak cores the floor clips towards white and the split would leave almost
    # no wood ("burnt" spots once the processor removes the reflection under a car): the wood
    # there is at least DIFFUSE_FLOOR × the wood of the surrounding floor
    wood_floor = np.where(floor, diffuse, 0.0).astype(np.float32)
    weight = cv2.GaussianBlur(floor.astype(np.float32), (0, 0), sigmaX=DIFFUSE_SIGMA[0], sigmaY=DIFFUSE_SIGMA[1])
    around = cv2.GaussianBlur(wood_floor, (0, 0), sigmaX=DIFFUSE_SIGMA[0], sigmaY=DIFFUSE_SIGMA[1]) / np.maximum(weight, 1e-6)
    diffuse = np.maximum(diffuse, DIFFUSE_FLOOR * around)
    # the reflection = everything that is not wood, as a smooth per-channel FRACTION of the plate:
    # applied to the plate's own pixels it follows the sharp streak cores exactly – a smoothed
    # absolute layer would leave bright/coloured remains of the cores when the processor removes it
    rest = np.clip(low - diffuse[..., None] * basis[:, 0], 0.0, None)
    fraction = np.clip(rest / np.maximum(low, 1e-6), 0.0, 1.0)
    if FRACTION_SIGMA > 0:
        fraction = cv2.GaussianBlur(fraction.astype(np.float32), (0, 0), FRACTION_SIGMA)
    # floor only, soft towards the excluded areas
    weight = cv2.GaussianBlur(floor.astype(np.float32), (0, 0), 1.5) * floor
    refl = lin * fraction * weight[..., None]
    info = {
        "wood": [round(float(x), 5) for x in wood],
        "light": [round(float(x), 5) for x in light],
        "blue": [round(float(x), 5) for x in blue],
        "reflectionMax": round(float(refl.max()), 4),
        "reflectionMeanFloor": round(float(refl[floor].mean()), 4),
        "floorMean": round(float(low[floor].mean()), 4),
    }
    if parts:
        return refl.astype(np.float32), info, fraction.astype(np.float32), weight.astype(np.float32)
    return refl.astype(np.float32), info


def limit_saturation(lin: np.ndarray, refl: np.ndarray, limits: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Scale the reflection per pixel (s in [0, 1]) so that ``lin − s·refl`` keeps g/r ≥ limits[0] and
    b/r ≥ limits[1] – removing it never leaves wood more saturated than the floor's own (a seam
    without gloss keeps its colour; a pixel that already is that saturated loses nothing)."""
    s = np.ones(lin.shape[:2], np.float32)
    for channel, rho in ((1, limits[0]), (2, limits[1])):
        num = lin[..., channel] - rho * lin[..., 0]
        den = refl[..., channel] - rho * refl[..., 0]
        cap = np.where(den > 1e-7, np.clip(num / np.maximum(den, 1e-7), 0.0, 1.0), 1.0)
        s = np.minimum(s, cap.astype(np.float32))
    return (refl * s[..., None]).astype(np.float32), s


def lift_streak_cores(lin: np.ndarray, refl: np.ndarray) -> tuple[np.ndarray, dict]:
    """Reduce the pass in the streak cores so that removing it leaves the wood of the unlit floor
    beside the streak, not darker and not greyer (``lin`` and ``refl`` linear, full size).

    In the cores of the light streaks his floor is near white; the colour split cannot tell how
    much wood is under that light and leaves too little (6–24 % darker and greyer than the wood
    between the streaks) – removed under a car, every streak would become a dark grey band. Per
    channel the wood left in a core is scaled up to the unlit wood next to it in the same rows
    (smooth gain, the grain of the plate's own pixels stays); the pass never becomes negative."""
    height, width = lin.shape[:2]
    wood = np.clip(lin - refl, 0.0, None)
    ps, rs, ds = (
        cv2.GaussianBlur(cv2.resize(x, PASS_SIZE, interpolation=cv2.INTER_AREA), (0, 0), GRAIN_SIGMA)
        for x in (lin, refl, wood)
    )
    share = ((rs @ LUMA) / np.maximum(ps @ LUMA, 1e-6)).astype(np.float32)
    floor = (rs @ LUMA) > 0.0  # the pass is 0 off the floor
    base = cv2.erode(np.where(floor, share, 9.0).astype(np.float32), np.ones((1, 2 * BASE_HALF + 1), np.uint8))
    base = cv2.GaussianBlur(np.where(base > 8.0, share, base), (0, 0), sigmaX=BASE_HALF / 2.0, sigmaY=3.0)
    unlit_w = (np.clip(1.0 - (share - base) / UNLIT_BAND, 0.0, 1.0) * floor).astype(np.float32)
    unlit = np.zeros_like(ds)
    done = np.zeros(share.shape, bool)
    for k in (1, 2, 4, 8):
        sx, sy = UNLIT_SIGMA[0] * k, UNLIT_SIGMA[1] * k
        num = cv2.GaussianBlur(ds * unlit_w[..., None], (0, 0), sigmaX=sx, sigmaY=sy)
        den = cv2.GaussianBlur(unlit_w, (0, 0), sigmaX=sx, sigmaY=sy)
        ok = (den > 0.05) & ~done
        unlit[ok] = (num / np.maximum(den, 1e-6)[..., None])[ok]
        done |= ok
    core = np.clip((share - base - CORE_OFFSET) / CORE_SPAN, 0.0, 1.0) * floor
    gain = 1.0 + core[..., None] * (np.clip(unlit / np.maximum(ds, 1e-6), 1.0, MAX_LIFT) - 1.0)
    gain = cv2.GaussianBlur(gain.astype(np.float32), (0, 0), 1.5)
    gain = cv2.resize(gain, (width, height), interpolation=cv2.INTER_LINEAR)
    lifted = np.where(refl > 0.0, lin - np.minimum(wood * gain, lin), refl)
    lifted = np.clip(lifted, 0.0, None).astype(np.float32)
    info = {
        "liftedShare": round(float((gain.max(axis=2) > 1.02)[cv2.resize(floor.astype(np.uint8), (width, height)) > 0].mean()), 4),
        "maxLift": round(float(gain.max()), 3),
        "removedByLift": round(float(((refl - lifted) @ LUMA).sum() / max(float((refl @ LUMA).sum()), 1e-6)), 4),
    }
    return lifted, info


def full_reflection(plate_rgb: np.ndarray, room: dict) -> tuple[np.ndarray, dict]:
    """The stored pass: the split's smooth fraction applied to the plate's own FULL-SIZE pixels
    (follows the plank seams, which a PASS_SIZE layer cannot), saturation-limited."""
    _, info, fraction, weight = split_reflection(plate_rgb, room, parts=True)
    height, width = plate_rgb.shape[:2]
    lin = _to_linear(plate_rgb.astype(np.float32) / 255.0).astype(np.float32)
    frac = cv2.resize(fraction, (width, height), interpolation=cv2.INTER_LINEAR)
    soft = np.clip(cv2.resize(weight, (width, height), interpolation=cv2.INTER_LINEAR), 0.0, 1.0)
    refl = lin * np.clip(frac, 0.0, 1.0) * soft[..., None]
    # the natural wood: floor pixels with (almost) no reflection
    share = (refl @ LUMA) / np.maximum(lin @ LUMA, 1e-6)
    wood_only = (soft > 0.99) & (share < WOOD_ONLY_FRACTION)
    red = np.maximum(lin[..., 0][wood_only], 1e-6)
    limits = (
        float(np.percentile(lin[..., 1][wood_only] / red, SATURATION_PERCENTILE)),
        float(np.percentile(lin[..., 2][wood_only] / red, SATURATION_PERCENTILE)),
    )
    limited, s = limit_saturation(lin, refl, limits)
    # removed under a car the streak cores keep the wood of the unlit floor beside them (no dark
    # grey bands); the saturation guard applies to the lifted pass again
    lifted, lift = lift_streak_cores(lin, limited)
    final, s2 = limit_saturation(lin, lifted, limits)
    on = soft > 0.5
    info = dict(info)
    info.update(
        {
            "saturationLimit": {"gr": round(limits[0], 4), "br": round(limits[1], 4), "woodPixels": int(wood_only.sum())},
            "limitedShare": round(float((s[on] < 0.999).mean()), 4),
            "removedByLimit": round(float(((refl - limited) @ LUMA)[on].sum() / max(float((refl @ LUMA)[on].sum()), 1e-6)), 4),
            "coreLift": {**lift, "limitedAfterLift": round(float((s2[on] < 0.999).mean()), 4)},
            "reflectionMax": round(float(final.max()), 4),
            "reflectionMeanFloor": round(float(final[on].mean()), 4),
        }
    )
    return final, info


def wall_albedo(plate_rgb: np.ndarray, measure: dict) -> list[float]:
    """Median linear colour of the wall around the branding (the matte print is lit by it)."""
    x0, y0, x1, y1 = measure["wordmark"]
    sx = plate_rgb.shape[1] / measure["image"][0]
    sy = plate_rgb.shape[0] / measure["image"][1]
    band = plate_rgb[int(y0 * sy) : int(measure["city"]["text"][3] * sy), int(x0 * sx) : int(x1 * sx)]
    lin = _to_linear(band.reshape(-1, 3).astype(np.float32) / 255.0)
    return [round(float(np.median(lin[:, i])), 4) for i in range(3)]


def branding_layout(measure: dict, room: dict) -> dict:
    """Fractions of the brand area that put the official logo and the city line where his were."""
    area = room["brandArea"]
    aw, ah = area["xMax"] - area["xMin"], area["zMax"] - area["zMin"]
    iw, ih = measure["image"]

    def wall(px, py):
        x, z = dl.wall_point(px / iw, py / ih)
        return (float(x) - area["xMin"]) / aw, (area["zMax"] - float(z)) / ah

    word = measure["wordmark"]
    (lx0, ltop), (lx1, _) = wall(word[0], word[1]), wall(word[2], word[1])
    city = measure["city"]
    (cx0, ctop), (cx1, cbottom) = wall(city["text"][0], city["capTop"]), wall(city["text"][2], city["baseline"])
    (rx0, _), (rx1, _) = wall(city["rulesOuter"][0], city["capTop"]), wall(city["rulesOuter"][1], city["capTop"])
    _, r0 = wall(0, city["ruleRows"][0])
    _, r1 = wall(0, city["ruleRows"][1])
    centre = 0.5 * (lx0 + lx1)
    text_w = cx1 - cx0
    rule_gap = city["ruleGap"] / iw * (dl.WALL_DISTANCE_M / dl.FOCAL_NORM) / aw
    rule_len = 0.5 * ((rx1 - rx0) - text_w) - rule_gap
    return {
        "logo": {"centerX": round(centre, 4), "top": round(ltop, 4), "maxWidth": round(lx1 - lx0, 4)},
        "city": {
            "centerX": round(centre, 4),
            "top": round(ctop, 4),
            "capHeight": round(cbottom - ctop, 4),
            "textWidth": round(text_w, 4),
            "ruleLength": round(rule_len, 4),
            "ruleGap": round(rule_gap, 4),
            "ruleThickness": round(r1 - r0, 4),
        },
    }


def fit_tracking(text: str, cap_height: float, width: float, aspect: float, weight: str = "medium") -> float:
    """Letter spacing (em) at which the bundled branding font sets `text` `width` wide (fractions of
    the brand area; `aspect` = brand area width / height) – the renderer's own text mask."""
    sys.path.insert(0, str(HERE.parent))
    from app.showroom.branding import TextConfig, _text_mask

    height = 2400

    def measured(tracking: float) -> float:
        mask = _text_mask(TextConfig(text=text, cap_height=cap_height, tracking=tracking, weight=weight), height)
        return mask.shape[1] / (height * aspect)

    lo, hi = 0.0, 1.5
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if measured(mid) < width else (lo, mid)
    return round(0.5 * (lo + hi), 3)


#: The variant preset: the standard preset with these changes (everything else identical).
VARIANT_NAME = "AutoExperten Händler-Hintergrund"
VARIANT_DESCRIPTION = (
    "Showroom-Hintergrund des Händlers (helle Paneelwand, blaue LED-Linien, Walnuss-Lamellen, "
    "Olivenbaum, glänzendes Parkett) mit offiziellem AutoExperten Logo und Schwetzingen."
)
#: Shadow and reflection of the variant in three strengths (the dealer: "the shadows underneath stay
#: far too black" with the earlier edgeOpacity 0.7 / underbodyFalloff 0.25 / ambientOpacity 0.6).
#: All three keep the wood's hue (tint 1, 1, 1) and limit the under-car terms to the car's footprint;
#: "mittel" is the recommendation and the variant file itself, the others are written next to it.
SHADOW_COMMON = {"tint": [1.0, 1.0, 1.0], "footprintReach": 0.3}
REFLECTION_COMMON = {"footprintReach": 0.3}
STRENGTHS = {
    "leicht": (
        {"contactOpacity": 0.85, "creaseOpacity": 0.7, "underbodyOpacity": 0.7, "edgeOpacity": 0.3,
         "underbodyFalloff": 0.08, "ambientOpacity": 0.2, "ambientReach": 0.3},
        {"strength": 1.2, "maxReflectance": 0.25, "fade": 0.4},
    ),
    "mittel": (
        {"contactOpacity": 0.9, "creaseOpacity": 0.8, "underbodyOpacity": 0.8, "edgeOpacity": 0.4,
         "underbodyFalloff": 0.1, "ambientOpacity": 0.3, "ambientReach": 0.4},
        {"strength": 1.5, "maxReflectance": 0.3, "fade": 0.5},
    ),
    "kraeftig": (
        {"contactOpacity": 0.92, "creaseOpacity": 0.85, "underbodyOpacity": 0.85, "edgeOpacity": 0.5,
         "underbodyFalloff": 0.14, "ambientOpacity": 0.4, "ambientReach": 0.5},
        {"strength": 1.5, "maxReflectance": 0.3, "fade": 0.6},
    ),
}  # fmt: skip
RECOMMENDED = "mittel"
STRENGTH_NAMES = {"leicht": "Schatten leicht", "mittel": "Schatten mittel", "kraeftig": "Schatten kräftig"}

#: The dealer's decision (2026-11-05): the car gets the guarded global studio light match
#: (app/pipeline/light.py, vehicleAdjustments.studioLight) – every field written out,
#: the same in all three shadow strengths.
VARIANT_STUDIO_LIGHT = {
    "enabled": True,
    "exposureStrength": 0.5,
    "keyTarget": 1.0,
    "maxExposureEv": 0.8,
    "contrast": 0.3,
    "shadows": 0.0,
    "highlightKnee": 0.85,
    "highlightSlope": 0.65,
    "whiteLimit": 0.985,
    "whiteBalanceStrength": 0.6,
    "maxWhiteBalance": 0.06,
    "maxHueShiftDeg": 2.5,
    "maxSaturationChange": 0.1,
    "maxNeutralColourShift": 1.5,
    "maxBlackLift": 4.0,
    "maxPaintLightnessShift": 6.0,
    "maxNewClipped": 0.002,
}


def strength_path(target: Path, strength: str) -> Path:
    """The variant file of a strength: the recommended one is `target` itself."""
    return target if strength == RECOMMENDED else target.with_name(f"{target.stem}-{strength}{target.suffix}")


def write_variant(standard: Path, target: Path, plates: str, layout: dict, room: dict) -> dict:
    """Write the variant in its three shadow strengths (``strength_path``); returns the recommended one."""
    data = variant_data(standard, plates, layout, room)
    target.parent.mkdir(parents=True, exist_ok=True)
    out = {}
    for strength, (shadow, reflection) in STRENGTHS.items():
        item = json.loads(json.dumps(data))
        if strength != RECOMMENDED:
            item["name"] = f"{VARIANT_NAME} ({STRENGTH_NAMES[strength]})"
        item["shadow"].update({**shadow, **SHADOW_COMMON})
        item["reflection"].update({**reflection, **REFLECTION_COMMON})
        strength_path(target, strength).write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out[strength] = item
    return out[RECOMMENDED]


def variant_data(standard: Path, plates: str, layout: dict, room: dict) -> dict:
    data = json.loads(standard.read_text(encoding="utf-8"))
    data["name"] = VARIANT_NAME
    data["description"] = VARIANT_DESCRIPTION
    data["background"] = {
        "plates": plates,
        "notes": "The dealer's own showroom image (made with an image AI): AI lettering removed "
        "(processor/showroom3d/dealer_clean.py), upscaled once to 2400 x 1800 (dealer_upscale.py, Lanczos), "
        "ONE plate.jpg for all eight exterior shots - one fixed level studio camera, the car is turned "
        "(dealer_layout.py); <shot>-shadow.png = Blender shadow pass of the proxy car at the shot's pose "
        "(render_dealer_passes.py), reflection.png = the floor's own reflection measured on the plate "
        "(dealer_backdrop.py). Official logo + Schwetzingen composited in wall space at the place of his lettering.",
    }
    area = room["brandArea"]
    aspect = (area["xMax"] - area["xMin"]) / (area["zMax"] - area["zMin"])
    city = layout["city"]
    branding = data["branding"]
    branding["logo"].update(
        {"centerX": layout["logo"]["centerX"], "top": layout["logo"]["top"], "maxWidth": layout["logo"]["maxWidth"], "maxHeight": 0.25}
    )
    branding["city"].update(
        {
            "text": "Schwetzingen",
            "centerX": city["centerX"],
            "top": city["top"],
            "capHeight": city["capHeight"],
            "tracking": fit_tracking("Schwetzingen", city["capHeight"], city["textWidth"], aspect),
            "weight": "medium",
            "mounted": True,
            "ruleLength": city["ruleLength"],
            "ruleGap": city["ruleGap"],
            "ruleThickness": city["ruleThickness"],
        }
    )
    for name in ("website", "phone"):  # no dealer contact data in listing photos (mobile.de)
        branding[name]["text"] = ""
    data["vehicleAdjustments"]["studioLight"] = dict(VARIANT_STUDIO_LIGHT)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plate", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--measure", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--standard", type=Path, help="the shipped preset (base of the variant)")
    parser.add_argument("--variant", type=Path, help="write the variant preset here (needs --standard)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    info = json.loads((args.render / "layout.json").read_text(encoding="utf-8"))
    if info != json.loads(json.dumps(dl.layout())):
        raise SystemExit("layout.json of the renders differs from dealer_layout.layout() – re-render the passes")
    measure = json.loads(args.measure.read_text(encoding="utf-8"))
    rgb = np.asarray(Image.open(args.plate).convert("RGB"))
    size = (rgb.shape[1], rgb.shape[0])
    if abs(size[0] / size[1] - 4 / 3) > 1e-3:
        raise SystemExit(f"{args.plate.name}: expected a 4:3 image, got {size}")
    Image.fromarray(rgb).save(args.out / PLATE_NAME, "JPEG", quality=args.quality, subsampling=0, optimize=True, icc_profile=pp._srgb_icc())

    room = info["room"]
    albedo = wall_albedo(rgb, measure)
    plates = {}
    base = pp._read16(args.render / "base.png")
    for key, shot in info["shots"].items():
        entry = dl.plate_entry(key, shot, size, room, PLATE_NAME, albedo)
        proxy = pp._read16(args.render / f"{key}-proxy.png")
        mult = pp.shadow_multiplier(base, proxy, entry)
        cv2.imwrite(str(args.out / f"{key}-shadow.png"), (mult * 65535.0 + 0.5).astype(np.uint16))
        entry["shadow"] = f"{key}-shadow.png"
        entry["shadowSize"] = [int(mult.shape[1]), int(mult.shape[0])]
        plates[key] = entry
        print(f"{key}: shadow min {mult.min():.3f}, wall clearance {shot['wallClearanceM']} m, width {entry['vehicle']['targetWidthRatio']}")

    # the processor subtracts the pass from the DECODED plate.jpg – measure it on exactly those pixels
    decoded = np.asarray(Image.open(args.out / PLATE_NAME).convert("RGB"))
    refl, split = full_reflection(decoded, room)
    # calibration exactly like the rendered plates: plate vs. "gloss-free" = plate − reflection
    # (at PASS_SIZE; only the reflectance samples are used – the stored pass is the full-size one)
    small = cv2.resize(refl, PASS_SIZE, interpolation=cv2.INTER_AREA)
    glossfree = _to_srgb(np.clip(plate_linear(decoded) - small, 0.0, 1.0)).astype(np.float32)
    first = next(iter(plates.values()))
    _, samples = pp.reflection_pass(decoded.astype(np.float32) / 255.0, glossfree, first)
    if len(samples) < 2:
        raise SystemExit("reflection calibration failed")
    if refl.min() < -REFLECTION_OFFSET - 1e-6 or refl.max() > 1.0 - REFLECTION_OFFSET + 1e-6:
        raise SystemExit("reflection pass outside the encodable range")
    out = cv2.cvtColor(np.clip(refl + REFLECTION_OFFSET, 0.0, 1.0), cv2.COLOR_RGB2BGR)
    code = np.round(out * (65535.0 / REFLECTION_STEP)) * REFLECTION_STEP
    cv2.imwrite(str(args.out / REFLECTION_NAME), np.clip(code, 0, 65535).astype(np.uint16), [cv2.IMWRITE_PNG_COMPRESSION, 9])
    for entry in plates.values():
        entry["reflection"] = REFLECTION_NAME
        entry["reflectionSize"] = [int(refl.shape[1]), int(refl.shape[0])]
        entry["reflectionOffset"] = REFLECTION_OFFSET
        entry["reflectance"] = samples
    ks = [k for _, k in samples]
    print(f"reflection: {split}, reflectance {min(ks):.3f}..{max(ks):.3f}")

    meta = {
        "version": 1,
        "plates": plates,
        "room": {
            "description": "AutoExperten dealer backdrop: the dealer's own showroom image (made with an image AI), "
            "lettering removed, upscaled; ONE fixed level studio camera (lens shift) for all eight shots, the car "
            "turned (processor/showroom3d/dealer_*.py, render_dealer_passes.py)",
            "wallPlane": "y = 0 (x right, z up), floor z = 0, metres",
            "hfovDeg": dl.HFOV_DEG,
            "camera": info["camera"],
            "features": room,
            "reflectionSplit": split,
        },
    }
    (args.out / "plates.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    layout = branding_layout(measure, room)
    (args.out / "branding.json").write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    print(f"branding: {json.dumps(layout)}")
    if args.variant is not None:
        if args.standard is None:
            raise SystemExit("--variant needs --standard")
        plates_rel = f"{args.out.name}/plates.json"
        variant = write_variant(args.standard, args.variant, plates_rel, layout, room)
        print(f"variant preset → {args.variant} (city tracking {variant['branding']['city']['tracking']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
