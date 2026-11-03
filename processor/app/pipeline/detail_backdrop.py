"""EXPERIMENTAL – detail close-ups with a defocused showroom background (opt-in).

Off by default (preset ``detailBackdrop.enabled``): detail shots then stay the
unchanged original, as before. When enabled for a detail shot (wheel, special
detail, optionally extra photos – never interior shots):

    BiRefNet alpha of the whole frame → colour-edge refinement (``matting``,
    never grows the model outline) → enclosed holes filled (see-through glass
    stays ORIGINAL pixels) → quality gate against the vehicle detector
    (semantic check only) → defocused crop of ONE showroom plate behind the
    part → composite → JPEG.

What is changed: only background pixels (alpha 0) are replaced by the plate
crop; the soft outline band is blended (edge decontamination + a light wrap of
a few px, as for exteriors). Every opaque car pixel away from that band is the
original pixel (tested); reflections of the hall in paint and glass are car
pixels and stay. Nothing is generated: the background is a crop of the fixed,
rendered 3D showroom, blurred with a Gaussian (depth of field), its mean
colour moved part of the way towards the photo's own background (light match),
with the photo's measured sensor noise added; the darkening the photo's own
floor shows next to the part (contact shadow) is measured and carried over –
never drawn.

Quality gate (``assess``) – the photo stays the original with a German hint
(code ``detail_backdrop_skipped``) when:
- there is hardly any background to replace (``minBackground``),
- the detector does not find the vehicle or does not agree with the part mask
  (``minVehicleIou`` – background objects merged into the mask, a lost piece),
- the detector sees vehicle where the part is not fully opaque and connected to
  the background at the photo border (``maxUndecided`` – a window/glass edge
  that cannot be decided, a cut-off mask; soft glass alpha counts too),
- the mask has a wide soft band along its outline (``maxEdgeSoftness``),
- another detected object lies against the part (``maxForeignTouch``),
and (``assess_backdrop``, on what the plate crop can show)
- the camera looks too steeply down at the floor for the plate's frontal floor
  (``maxHorizonDrop``),
- a part standing over open floor shows no contact shadow the plate floor can
  carry, or one darker than it can carry (``minContactShadow``,
  ``maxShadowClipped``) – it would float or get a light halo.

Limits (see the study notes in the task report): the plate crop is not in the
photo's perspective – the blur hides it only for real close-ups and the
perspective gate only rejects steep top-down views; the floor line is placed
at a configured height (clamped above the part's floor contact), not measured.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from ..presets import BackgroundProvider, DetailBackdropConfig, Preset, PresetConfigError, detail_backdrop_applies
from .color import linear_to_srgb, linear_to_u8, luminance, srgb_to_linear
from .debug import NULL_DEBUG, DebugSink
from .detection import MIN_SCORE, Instance
from .harmonize import decontaminate_edges, light_wrap
from .mask import QualityWarning
from .matting import refine_edges
from .segmentation import SegmentationError, VehicleSegmenter

log = logging.getLogger(__name__)

__all__ = [
    "DetailOutcome",
    "assess",
    "assess_backdrop",
    "compose",
    "detail_backdrop_applies",
    "part_alpha",
    "plan_backdrop",
    "run_detail",
]

SKIP_CODE = "detail_backdrop_skipped"

#: Soft alpha farther than this share of the long edge from the real background is opaque.
HOLE_RIM_FRACTION = 0.004
#: Alpha below this is blended with the plate (not an untouched car pixel).
OPAQUE = 0.99
#: Working long edge of the gate's mask comparisons and the contact-shadow measurement.
WORK_PX = 1024
#: The detector's instance outline is about one mask cell coarse: compare inside a band of this many cells.
CELL_TOLERANCE = 2.5
#: Another object counts as touching within this share of the long edge of the part.
TOUCH_PX_FRACTION = 0.01
#: A detected instance is the same vehicle (duplicate class, e.g. car + truck) at this IoU with it.
DUPLICATE_IOU = 0.5
#: Light match limits: luminance ±1 EV, per-channel ratio ±12 % around the luminance gain.
MAX_EV = 1.0
MAX_CHANNEL = 0.12
#: Grain: the noise floor is the 10th percentile of local high-pass std over 16-px blocks, at most 3/255.
MAX_GRAIN = 3.0 / 255.0
#: Contact shadow: measured darkening below this is floor texture (no shadow) …
SHADOW_DEAD_ZONE = 0.1
#: … never darker than this multiplier (deeper photo shadows than that → gate "ground_contact") …
SHADOW_FLOOR = 0.15
#: … only within this share of the long edge of the part; local / wide scale of the measurement.
SHADOW_REACH = 0.1
SHADOW_LOCAL = 0.012
SHADOW_WIDE = 0.08
#: … the contact band (the gate's measurement) reaches this share of the long edge below the outline;
SHADOW_CONTACT = 0.02
#: … the unshadowed floor of a row: this upper percentile of the background brightness over ±this
#: share of the long edge of rows, where at least this share of those rows is background;
SHADOW_REFERENCE_PERCENTILE = 80
SHADOW_REFERENCE_BAND = 0.02
SHADOW_REFERENCE_MIN = 0.15
#: … measured at this working long edge (px).
SHADOW_WORK_PX = 512.0
#: Floor line: at least this share of the height above where the part meets the floor …
GROUND_MARGIN = 0.03
#: … never higher than this (fraction of the height) …
MIN_HORIZON = 0.1
#: … measured as this percentile of the per-column lowest part pixel over columns whose
#: background below is at least GROUND_MIN_GAP of the height, in at least GROUND_MIN_COLUMNS of them.
GROUND_PERCENTILE = 5
GROUND_MIN_GAP = 0.02
GROUND_MIN_COLUMNS = 0.05
#: The crop may zoom in to this share of the plate height to place a high floor line.
MIN_CROP_HEIGHT = 0.2
#: Light wrap width (px at the output long edge / 1000).
WRAP_PX_PER_1000 = 1.5

MESSAGES = {
    "no_background": "Detailfoto: kaum Hintergrund zu ersetzen – das Original bleibt unverändert.",
    "vehicle_not_detected": "Detailfoto: Fahrzeugteil nicht eindeutig erkannt – Hintergrund nicht ersetzt.",
    "mask_mismatch": "Detailfoto: Fahrzeugteil nicht sauber vom Hintergrund trennbar – Hintergrund nicht ersetzt.",
    "edge_undecided": "Detailfoto: Glas- oder Bildkante nicht eindeutig – Hintergrund nicht ersetzt.",
    "edge_soft": "Detailfoto: Kontur des Fahrzeugteils unscharf – Hintergrund nicht ersetzt.",
    "object_touching": "Detailfoto: Ein anderes Objekt berührt das Fahrzeugteil – Hintergrund nicht ersetzt.",
    "segmentation_failed": "Detailfoto: Fahrzeugteil nicht erkannt – Hintergrund nicht ersetzt.",
    "showroom_unavailable": "Detailfoto: Showroom-Hintergrund nicht verfügbar – das Original bleibt unverändert.",
    "perspective": "Detailfoto: Aufnahmewinkel passt nicht zum Showroom-Boden – Hintergrund nicht ersetzt.",
    "ground_contact": "Detailfoto: Bodenkontakt und Schatten nicht übertragbar – Hintergrund nicht ersetzt.",
}


@dataclass
class GateResult:
    passed: bool
    reason: str | None = None
    metrics: dict = field(default_factory=dict)

    def warning(self) -> QualityWarning | None:
        return None if self.passed else skip_warning(self.reason or "mask_mismatch")

    def as_dict(self) -> dict:
        return {"passed": self.passed, "reason": self.reason, **self.metrics}


def skip_warning(reason: str) -> QualityWarning:
    return QualityWarning(SKIP_CODE, MESSAGES.get(reason, MESSAGES["mask_mismatch"]))


# --------------------------------------------------------------------------- mask


def part_alpha(rgb: np.ndarray, coarse: np.ndarray) -> tuple[np.ndarray, dict]:
    """The part's alpha: the model alpha snapped onto colour edges (``refine_edges``, never
    grown beyond the model outline), enclosed holes filled – background seen through glass
    or between parts stays the ORIGINAL photo, never the plate."""
    alpha, info = refine_edges(rgb, coarse)
    solid = (alpha >= 0.5).astype(np.uint8)
    holes = enclosed_holes(solid)
    # the soft rim of a filled hole (inside a window frame) is car too: only pixels next to the
    # REAL background (reachable from the photo border) may stay partly transparent
    outside = (solid == 0) & ~holes
    rim = max(3, int(round(HOLE_RIM_FRACTION * max(alpha.shape))))
    near_outside = cv2.dilate(outside.astype(np.uint8), _disk(rim)) > 0
    opaque = ~near_outside
    changed = opaque & (alpha < 1.0)
    if changed.any():
        alpha = np.where(opaque, 1.0, alpha).astype(np.float32)
    info = {**info, "filledHoleFraction": round(float(np.mean(changed)), 5)}
    return alpha, info


def border_connected(mask: np.ndarray) -> np.ndarray:
    """The components of ``mask`` (8-connected) that touch the photo border."""
    count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros(mask.shape, bool)
    keep = np.zeros(count, bool)
    keep[np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))] = True
    keep[0] = False  # label 0 = outside ``mask``
    return keep[labels]


def enclosed_holes(solid: np.ndarray) -> np.ndarray:
    """Background components (of ``solid`` == 0) that do not touch the photo border."""
    count, labels = cv2.connectedComponents((solid == 0).astype(np.uint8), connectivity=4)
    if count <= 1:
        return np.zeros(solid.shape, bool)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    keep = np.ones(count, bool)
    keep[border] = False
    keep[0] = False  # label 0 = the solid part itself
    return keep[labels]


# --------------------------------------------------------------------------- gate


def _work(mask: np.ndarray, scale: float) -> np.ndarray:
    h, w = mask.shape
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return cv2.resize(mask.astype(np.float32), size, interpolation=cv2.INTER_AREA) >= 0.5


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = float(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b)) / union if union else 0.0


def _disk(radius: int) -> np.ndarray:
    radius = max(int(radius), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def assess(
    alpha: np.ndarray, coarse: np.ndarray, instances: list[Instance] | None, cfg: DetailBackdropConfig
) -> GateResult:
    """Quality gate of the part mask (see module docstring). ``instances``: the detector's
    full-frame view (None = no detector configured → not decidable → skipped)."""
    height, width = alpha.shape
    scale = min(1.0, WORK_PX / float(max(height, width)))
    part = _work(alpha >= 0.5, scale)
    wh, ww = part.shape
    background = float(np.mean(alpha < 0.05))
    metrics: dict = {"backgroundFraction": round(background, 4)}
    if background < cfg.min_background:
        return GateResult(False, "no_background", metrics)

    # soft band of the MODEL alpha along the part's outline (outside filled holes)
    soft = (coarse > 0.05) & (coarse < 0.95) & (alpha < 0.999)
    outline = cv2.morphologyEx((alpha >= 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    outline_px = max(float(np.count_nonzero(outline)) / 2.0, 1.0)
    softness = float(np.count_nonzero(soft)) / outline_px / float(max(height, width))
    metrics["edgeSoftness"] = round(softness, 5)
    if softness > cfg.max_edge_softness:
        return GateResult(False, "edge_soft", metrics)

    if not instances:
        return GateResult(False, "vehicle_not_detected", metrics)
    candidates = [i for i in instances if i.score >= MIN_SCORE]
    masks = [i.logits(ww, wh) > 0 for i in candidates]
    scored = [(_iou(m, part), n) for n, m in enumerate(masks) if candidates[n].is_vehicle]
    if not scored:
        return GateResult(False, "vehicle_not_detected", metrics)
    iou, index = max(scored)
    vehicle = masks[index]
    cells = max(int(np.shape(candidates[index].mask)[-1]), 1)
    tolerance = int(np.ceil(CELL_TOLERANCE * max(ww, wh) / cells))
    metrics.update(vehicleIou=round(iou, 3), vehicleScore=round(candidates[index].score, 3), toleranceWorkPx=tolerance)
    if iou < cfg.min_vehicle_iou:
        return GateResult(False, "mask_mismatch", metrics)

    # detector vehicle (well inside its coarse outline) where the part is not fully opaque AND
    # connected to the real background (reachable from the photo border): there the plate would
    # be blended in – a glass/window edge at the border, a lost or cut-off piece. Semi-transparent
    # alpha counts too (it is blended just as well); filled holes are opaque and do not count.
    core = cv2.erode(vehicle.astype(np.uint8), _disk(tolerance)) > 0
    size = (ww, wh)
    open_alpha = cv2.resize(alpha.astype(np.float32), size, interpolation=cv2.INTER_AREA) < OPAQUE
    undecided = core & border_connected(open_alpha)
    metrics["undecidedFraction"] = round(float(np.mean(undecided)), 5)
    if float(np.mean(undecided)) > cfg.max_undecided:
        return GateResult(False, "edge_undecided", metrics)

    # other objects against the part: their mask within a few px of it
    near = cv2.dilate(part.astype(np.uint8), _disk(max(2, int(TOUCH_PX_FRACTION * max(ww, wh))))) > 0
    touching = []
    for n, mask in enumerate(masks):
        area = float(np.count_nonzero(mask))
        if n == index or area == 0:
            continue
        if candidates[n].is_vehicle and _iou(mask, vehicle) >= DUPLICATE_IOU:
            continue  # the same car as another class
        # the object's own pixels next to (or inside) the part, beyond the coarse outline of the vehicle
        beyond = mask & near & ~(cv2.dilate(vehicle.astype(np.uint8), _disk(tolerance)) > 0)
        inside = mask & part
        share = max(float(np.count_nonzero(beyond)), float(np.count_nonzero(inside))) / area
        if share > cfg.max_foreign_touch:
            touching.append({**candidates[n].as_dict(), "share": round(share, 3)})
    metrics["touching"] = touching
    if touching:
        return GateResult(False, "object_touching", metrics)
    return GateResult(True, None, metrics)


# --------------------------------------------------------------------------- backdrop


@dataclass(frozen=True)
class Crop:
    x0: float
    y0: float
    width: float
    height: float
    #: Where the plate's floor line ended up (fraction of the crop height).
    horizon: float

    def as_dict(self) -> dict:
        return {
            "box": [round(self.x0, 1), round(self.y0, 1), round(self.x0 + self.width, 1), round(self.y0 + self.height, 1)],
            "horizon": round(self.horizon, 3),
        }


def plan_crop(
    plate_w: int, plate_h: int, floor_top: np.ndarray, aspect: float, cfg: DetailBackdropConfig, *, mirrored: bool = False
) -> Crop:
    """The plate window behind the part: ``cropHeight`` of the plate at the photo's aspect,
    centred on ``centerX`` (``1 - centerX`` when ``mirrored``: the photo's background lies left
    of the part), the plate's floor line at ``horizon`` of its height (clamped into the plate)."""
    height = cfg.crop_height * plate_h
    width = height * aspect
    if width > plate_w:
        width = float(plate_w)
        height = width / aspect
    centre = 1.0 - cfg.center_x if mirrored else cfg.center_x

    def place(width: float, height: float) -> Crop:
        x0 = float(np.clip(centre * plate_w - 0.5 * width, 0.0, plate_w - width))
        columns = floor_top[int(x0) : int(np.ceil(x0 + width))]
        floor_row = float(np.median(columns)) if columns.size else 0.6 * plate_h
        y0 = float(np.clip(floor_row - cfg.horizon * height, 0.0, plate_h - height))
        return Crop(x0, y0, width, height, (floor_row - y0) / height)

    crop = place(width, height)
    if crop.horizon > cfg.horizon + 1e-3:
        # not enough floor below the plate's floor line for a high one: zoom in until it fits
        floor_row = crop.horizon * crop.height + crop.y0
        smaller = max((plate_h - floor_row) / (1.0 - cfg.horizon), MIN_CROP_HEIGHT * plate_h)
        if smaller < height:
            crop = place(smaller * aspect, smaller)
    return crop


def _resample(plate_rgb: np.ndarray, crop: Crop, width: int, height: int) -> np.ndarray:
    sx, sy = width / crop.width, height / crop.height
    matrix = np.array([[sx, 0.0, -crop.x0 * sx], [0.0, sy, -crop.y0 * sy]], np.float32)
    interpolation = cv2.INTER_AREA if sx < 1.0 else cv2.INTER_CUBIC
    if sx < 1.0:  # area resampling needs an axis-aligned crop + resize
        x0, y0 = int(round(crop.x0)), int(round(crop.y0))
        x1, y1 = int(round(crop.x0 + crop.width)), int(round(crop.y0 + crop.height))
        return cv2.resize(plate_rgb[y0:y1, x0:x1], (width, height), interpolation=interpolation)
    return cv2.warpAffine(plate_rgb, matrix, (width, height), flags=interpolation, borderMode=cv2.BORDER_REFLECT)


def ground_line(part: np.ndarray) -> float | None:
    """Highest row (px, robust: GROUND_PERCENTILE) where the part meets the floor: outline pixels
    whose background below runs down to the photo's bottom edge (under the sill, a tyre, the
    bumper). None when the part never stands over open background (e.g. it fills the bottom)."""
    height, width = part.shape
    # per column: the last part pixel, and whether everything below it is background
    has = part.any(axis=0)
    last = np.where(has, height - 1 - np.argmax(part[::-1], axis=0), -1)
    open_below = has & (last < height - 1 - GROUND_MIN_GAP * height)
    if np.count_nonzero(open_below) < GROUND_MIN_COLUMNS * width:
        return None
    return float(np.percentile(last[open_below], GROUND_PERCENTILE))


def defocus(
    linear: np.ndarray, horizon_y: float, sigma_wall: float, floor_factor: float, focus_y: float | None = None
) -> np.ndarray:
    """Depth of field: the wall blurred with ``sigma_wall``, the floor from that at the floor
    line down to ``floor_factor`` × it at ``focus_y`` (where the part meets the floor – the floor
    there is as far away as the part; default the bottom row) and below (blend of three blur
    levels per row)."""
    if sigma_wall <= 0.3:
        return linear
    height = linear.shape[0]
    focus = float(height) if focus_y is None else float(np.clip(focus_y, horizon_y + 1.0, height))
    levels = [sigma_wall, sigma_wall * (1 + floor_factor) / 2, sigma_wall * floor_factor]
    blurred = [linear if s <= 0.3 else cv2.GaussianBlur(linear, (0, 0), s) for s in levels]
    rows = np.arange(height, dtype=np.float32)
    t = np.clip((rows - horizon_y) / max(focus - horizon_y, 1.0), 0.0, 1.0) * 2.0  # 0 wall … 2 focus
    w1 = np.clip(1.0 - np.abs(t - 1.0), 0.0, 1.0)
    w0 = np.clip(1.0 - t, 0.0, 1.0)
    w2 = np.clip(t - 1.0, 0.0, 1.0)
    out = blurred[0] * w0[:, None, None] + blurred[1] * w1[:, None, None] + blurred[2] * w2[:, None, None]
    return out.astype(np.float32)


def light_match(backdrop: np.ndarray, photo: np.ndarray, background: np.ndarray, strength: float) -> tuple[np.ndarray, dict]:
    """Move the backdrop's mean colour (where the photo shows background) part of the way
    towards the photo's own background – luminance within ±MAX_EV, colour within ±MAX_CHANNEL."""
    if strength <= 0 or not background.any():
        return backdrop, {"gain": [1.0, 1.0, 1.0]}
    m_photo = photo[background].mean(axis=0) + 1e-4
    m_plate = backdrop[background].mean(axis=0) + 1e-4
    lum_gain = float(np.clip(luminance(m_photo) / luminance(m_plate), 2.0**-MAX_EV, 2.0**MAX_EV)) ** strength
    ratio = (m_photo / luminance(m_photo)) / (m_plate / luminance(m_plate))
    chroma = np.clip(ratio, 1.0 - MAX_CHANNEL, 1.0 + MAX_CHANNEL) ** strength
    gain = (lum_gain * chroma).astype(np.float32)
    return (backdrop * gain).astype(np.float32), {"gain": [round(float(g), 3) for g in gain]}


def noise_level(rgb: np.ndarray, background: np.ndarray) -> float:
    """Sensor noise of the photo's background (sRGB 0..1): the 10th percentile of the local
    std of a fine high-pass over 16-px blocks – flat areas, not texture."""
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    high = grey - cv2.GaussianBlur(grey, (0, 0), 1.0)
    block = 16
    h, w = (grey.shape[0] // block) * block, (grey.shape[1] // block) * block
    if h == 0 or w == 0:
        return 0.0
    tiles = high[:h, :w].reshape(h // block, block, w // block, block).std(axis=(1, 3))
    inside = background[:h, :w].reshape(h // block, block, w // block, block).all(axis=(1, 3))
    if np.count_nonzero(inside) < 8:
        return 0.0
    return float(min(np.percentile(tiles[inside], 10) * 1.6, MAX_GRAIN))  # high-pass at σ=1 keeps ~60 %


def contact_shadow(linear: np.ndarray, part: np.ndarray, strength: float) -> tuple[np.ndarray, dict]:
    """Multiplier (h, w) of the darkening the photo's OWN floor shows under/next to the part,
    faded out with the distance to the part's LOWER outline (where it meets or hangs over the
    floor). 1 = no shadow. Measured, never drawn: without darkening in the photo it is 1.

    The darkening is the local brightness of background pixels against the UNSHADOWED floor
    of the same rows: the brighter of the row's wide mean and a robust upper percentile of the
    row band's background (a cast shadow that covers most of a row is not cancelled by its own
    row mean; a floor that gets darker towards the wall is, row by row).

    ``info["contactDarkening"]``: the photo's darkening right at the lower outline (upper
    percentile over the contact band) – the gate's "does the part stand on a measurable
    shadow" value; ``info["clipped"]``: share of that band darker than SHADOW_FLOOR allows."""
    height, width = part.shape
    flat = np.ones((height, width), np.float32)
    none = {"maxDarkening": 0.0, "contactDarkening": None, "clipped": 0.0}
    scale = min(1.0, SHADOW_WORK_PX / max(height, width))
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    lum = cv2.resize(luminance(linear), size, interpolation=cv2.INTER_AREA)
    small = cv2.resize(part.astype(np.float32), size, interpolation=cv2.INTER_AREA)
    bg = small <= 0.001
    bg_f = bg.astype(np.float32)
    long_edge = max(size)
    # the part's lower outline: part pixels with background right below
    solid = small >= 0.5
    lower = solid.copy()
    lower[:-1] &= ~solid[1:]
    lower[-1] = False
    if not lower.any() or not bg.any():
        return flat, none

    def normalised(sigma_x: float, sigma_y: float) -> np.ndarray:
        num = cv2.GaussianBlur(lum * bg_f, (0, 0), sigmaX=sigma_x, sigmaY=sigma_y)
        den = cv2.GaussianBlur(bg_f, (0, 0), sigmaX=sigma_x, sigmaY=sigma_y)
        return num / np.maximum(den, 1e-3)

    local = normalised(SHADOW_LOCAL * long_edge, SHADOW_LOCAL * long_edge)
    rows = normalised(SHADOW_WIDE * long_edge, 0.5)  # the same rows farther away
    reference = np.maximum(rows, _row_upper(local, bg))
    ratio = np.clip(local / np.maximum(reference, 1e-4), 0.0, 1.0)
    measured = np.clip((1.0 - ratio) - SHADOW_DEAD_ZONE, 0.0, None) / (1.0 - SHADOW_DEAD_ZONE) * bg_f
    distance = cv2.distanceTransform((~lower).astype(np.uint8), cv2.DIST_L2, 5)
    band = bg & (distance <= SHADOW_CONTACT * long_edge)
    contact = float(np.percentile(measured[band], 90)) if np.count_nonzero(band) >= 8 else None
    clipped = float(np.mean(measured[band] > 1.0 - SHADOW_FLOOR)) if contact is not None else 0.0
    # full strength up to half the reach (a cast shadow under a sill keeps its own extent, no
    # halo that follows the outline), then a smooth fade to 0 at the reach
    t = np.clip((distance - 0.5 * SHADOW_REACH * long_edge) / (0.5 * SHADOW_REACH * long_edge), 0.0, 1.0)
    fade = 1.0 - t * t * (3.0 - 2.0 * t)
    # normalised blur over the background only: the measurement continues up to (and under) the
    # soft outline instead of falling to 0 there (which would leave a LIGHT rim along the part)
    sigma = 0.5 * SHADOW_LOCAL * long_edge
    num = cv2.GaussianBlur(measured * fade * bg_f, (0, 0), sigma)
    den = cv2.GaussianBlur(bg_f, (0, 0), sigma)
    darkening = np.where(den > 0.02, num / np.maximum(den, 1e-3), 0.0) * max(strength, 0.0)
    multiplier = np.clip(1.0 - darkening, SHADOW_FLOOR, 1.0).astype(np.float32)
    full = cv2.resize(multiplier, (width, height), interpolation=cv2.INTER_LINEAR)
    info = {
        "maxDarkening": round(float(1.0 - multiplier.min()), 3),
        "contactDarkening": None if contact is None else round(contact, 3),
        "clipped": round(clipped, 3),
    }
    return full, info


def _row_upper(local: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Per row: the SHADOW_REFERENCE_PERCENTILE of the background's local brightness over a band
    of rows (the unshadowed floor of that height), 0 where the band has too little background."""
    height, width = local.shape
    half = max(1, int(round(SHADOW_REFERENCE_BAND * max(height, width))))
    out = np.zeros(height, np.float32)
    counts = bg.sum(axis=1)
    for y in range(0, height, max(1, half // 2)):
        y0, y1 = max(0, y - half), min(height, y + half + 1)
        if counts[y0:y1].sum() < SHADOW_REFERENCE_MIN * width * (y1 - y0):
            continue
        out[y : y + max(1, half // 2)] = np.percentile(local[y0:y1][bg[y0:y1]], SHADOW_REFERENCE_PERCENTILE)
    return out[:, None]


@dataclass
class BackdropPlan:
    """Where the plate goes behind the part and what the photo's floor tells (before any pixel
    work): the crop, its side, where the part meets the floor, the measured contact shadow."""

    crop: Crop
    mirrored: bool
    #: Row (px) where the part meets the floor (None: it never stands over open background).
    ground: float | None
    background: np.ndarray
    shadow: np.ndarray
    shadow_info: dict

    def as_dict(self, height: int) -> dict:
        return {
            "crop": {
                **self.crop.as_dict(),
                "mirrored": self.mirrored,
                "groundY": None if self.ground is None else round(self.ground / height, 3),
            },
            "contactShadow": self.shadow_info,
        }


def plan_backdrop(
    rgb: np.ndarray, alpha: np.ndarray, plate_size: tuple[int, int], floor_top: np.ndarray, cfg: DetailBackdropConfig
) -> BackdropPlan:
    """The crop of the plate (``plate_size`` = (width, height)) behind the part and the photo's
    measured contact shadow."""
    height, width = alpha.shape
    plate_w, plate_h = plate_size
    background = cv2.erode((alpha <= 0.0).astype(np.uint8), _disk(3)) > 0
    # the room's side with the slat panel/plant goes where the photo shows its background
    columns = np.nonzero(background.any(axis=0))[0]
    weights = background.sum(axis=0)[columns] if columns.size else None
    mirrored = bool(columns.size) and float(np.average(columns, weights=weights)) < 0.5 * width
    # the plate's floor line must lie above where the part meets the floor in the photo
    ground = ground_line(alpha >= 0.5)
    horizon = cfg.horizon
    if ground is not None:
        horizon = float(np.clip(min(horizon, ground / height - GROUND_MARGIN), MIN_HORIZON, cfg.horizon))
    crop = plan_crop(plate_w, plate_h, floor_top, width / height, replace(cfg, horizon=horizon), mirrored=mirrored)
    shadow, shadow_info = contact_shadow(srgb_to_linear(rgb), alpha >= 0.5, cfg.contact_shadow)
    return BackdropPlan(crop, mirrored, ground, background, shadow, shadow_info)


def assess_backdrop(plan: BackdropPlan, cfg: DetailBackdropConfig) -> GateResult:
    """Second gate, on what the plate crop can show: the plate floor is seen from a standing,
    frontal camera – a photo looking steeply down at the floor (the part meets the floor high in
    the frame, so the floor line had to move far above ``horizon``) would get a floor in the
    wrong perspective (``maxHorizonDrop``). A part standing over open floor must show a contact
    shadow the plate floor can carry (``minContactShadow`` – else it floats; ``maxShadowClipped``
    – a shadow deeper than SHADOW_FLOOR, e.g. a dark floor under the car, would get a light
    halo). Nothing is drawn to make up for either: the photo stays the original."""
    drop = cfg.horizon - plan.crop.horizon
    contact = plan.shadow_info.get("contactDarkening")
    clipped = float(plan.shadow_info.get("clipped") or 0.0)
    metrics = {"horizonDrop": round(drop, 3), "contactDarkening": contact, "shadowClipped": round(clipped, 3)}
    if drop > cfg.max_horizon_drop + 1e-6:
        return GateResult(False, "perspective", metrics)
    if plan.ground is not None and (contact is None or contact < cfg.min_contact_shadow or clipped > cfg.max_shadow_clipped):
        return GateResult(False, "ground_contact", metrics)
    return GateResult(True, None, metrics)


@dataclass
class DetailComposite:
    rgb: np.ndarray
    background: np.ndarray
    crop: Crop
    info: dict


def compose(
    rgb: np.ndarray,
    alpha: np.ndarray,
    plate_rgb: np.ndarray,
    floor_top: np.ndarray,
    cfg: DetailBackdropConfig,
    *,
    plan: BackdropPlan | None = None,
    light_wrap_strength: float = 0.1,
    seed: int = 0,
) -> DetailComposite:
    """The original ``rgb`` (uint8) in front of the defocused plate crop, at the photo's size.
    Opaque car pixels away from the outline band are returned unchanged (bit for bit)."""
    height, width = alpha.shape
    plate_h, plate_w = plate_rgb.shape[:2]
    if plan is None:
        plan = plan_backdrop(rgb, alpha, (plate_w, plate_h), floor_top, cfg)
    crop, background = plan.crop, plan.background
    long_edge = float(max(width, height))
    plate = srgb_to_linear(_resample(plate_rgb, crop, width, height))
    horizon_y = crop.horizon * height
    backdrop = defocus(plate, horizon_y, cfg.blur * long_edge, cfg.floor_blur, plan.ground)
    photo = srgb_to_linear(rgb)
    backdrop, light = light_match(backdrop, photo, background, cfg.light_match)
    floor = (np.arange(height, dtype=np.float32) >= horizon_y)[:, None]
    backdrop = backdrop * np.where(floor, plan.shadow, 1.0)[..., None]
    sigma = noise_level(rgb, background) * cfg.grain
    if sigma > 0:
        srgb = linear_to_srgb(backdrop) + np.random.default_rng(seed).normal(0.0, sigma, backdrop.shape).astype(np.float32)
        backdrop = srgb_to_linear(np.clip(srgb, 0.0, 1.0))
    car = decontaminate_edges(photo, alpha, radius=max(1.5, long_edge / 1000.0))
    car = light_wrap(car, alpha, backdrop, light_wrap_strength, WRAP_PX_PER_1000 * long_edge / 1000.0)
    a = alpha[..., None]
    out = linear_to_u8(car * a + backdrop * (1.0 - a))
    # bit-exact original wherever the car is opaque and outside the wrap/feather band
    band = int(np.ceil(4 * WRAP_PX_PER_1000 * long_edge / 1000.0)) + 1
    untouched = cv2.erode((alpha >= 0.999).astype(np.uint8), _disk(band)) > 0
    out[untouched] = rgb[untouched]
    info = {
        **plan.as_dict(height),
        "blurSigmaPx": round(cfg.blur * long_edge, 2),
        "lightMatch": light,
        "grainSigma": round(sigma * 255.0, 2),
    }
    return DetailComposite(rgb=out, background=linear_to_u8(backdrop), crop=crop, info=info)


# --------------------------------------------------------------------------- pipeline step


@dataclass
class DetailOutcome:
    """The detail photo to export (the composite, or the original when skipped) and why."""

    rgb: np.ndarray
    applied: bool
    warnings: list[QualityWarning]
    meta: dict

    @property
    def shot_kind(self) -> str:
        return "detail_backdrop" if self.applied else "detail_passthrough"


def _fit(rgb: np.ndarray, long_edge_max: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    scale = min(1.0, long_edge_max / float(max(height, width)))
    if scale >= 1.0:
        return rgb
    size = (int(round(width * scale)), int(round(height * scale)))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)


def run_detail(
    rgb: np.ndarray,
    *,
    preset: Preset,
    segmenter: VehicleSegmenter,
    backgrounds: BackgroundProvider,
    debug: DebugSink = NULL_DEBUG,
    progress: Callable[[float, str], None] = lambda _p, _s: None,
) -> DetailOutcome:
    """The experimental detail-backdrop step for one decoded photo (only called for shots
    ``detail_backdrop_applies`` accepts). Never raises for an unclear photo: it is returned as
    the original (at most ``output.longEdgeMax``) with a ``detail_backdrop_skipped`` hint.
    A missing segmentation model (ModelUnavailableError) still fails the job, as for exteriors."""
    cfg = preset.detail_backdrop
    rgb = _fit(rgb, preset.output.long_edge_max)
    times: dict[str, int] = {}
    started = time.perf_counter()

    def mark(step: str) -> None:
        nonlocal started
        now = time.perf_counter()
        times[step] = int(round((now - started) * 1000))
        started = now

    def skip(reason: str, **extra) -> DetailOutcome:
        meta = {"applied": False, "reason": reason, **extra, "timingsMs": times}
        return DetailOutcome(rgb=rgb, applied=False, warnings=[skip_warning(reason)], meta=meta)

    try:
        plate = backgrounds.plate_set(preset).plate(cfg.plate)
        showroom = backgrounds.get(preset, cfg.plate, plate.size[0], plate.size[1])
    except PresetConfigError as error:  # incl. ShowroomUnavailableError – the original stays
        log.warning("Detail backdrop without showroom plate: %s", error)
        return skip("showroom_unavailable")
    mark("plate")

    progress(0.1, "detect")
    detector = getattr(segmenter, "detector", None)
    instances = detector.detect(rgb) if detector is not None else None
    mark("detect")
    if not instances or not any(i.is_vehicle and i.score >= MIN_SCORE for i in instances):
        return skip("vehicle_not_detected")  # before the expensive segmentation (interior extras)

    progress(0.2, "segment")
    try:
        coarse = segmenter.segment(rgb)
    except SegmentationError:
        return skip("segmentation_failed")
    if coarse.shape != rgb.shape[:2]:
        raise ValueError("segmenter returned a mask of the wrong size")
    mark("segment")

    progress(0.6, "mask")
    alpha, alpha_info = part_alpha(rgb, coarse)
    debug.gray("mask.png", alpha)
    gate = assess(alpha, coarse, instances, cfg)
    mark("mask")
    mask_meta = {
        "edgeConfidence": alpha_info.get("edgeConfidence"),
        "filledHoleFraction": alpha_info.get("filledHoleFraction"),
    }
    if not gate.passed:
        return skip(gate.reason or "mask_mismatch", gate=gate.as_dict(), mask=mask_meta)

    progress(0.7, "plan")
    plan = plan_backdrop(rgb, alpha, (showroom.rgb.shape[1], showroom.rgb.shape[0]), showroom.floor_top, cfg)
    scene = assess_backdrop(plan, cfg)
    gate = replace(gate, passed=scene.passed, reason=scene.reason, metrics={**gate.metrics, **scene.metrics})
    mark("plan")
    if not gate.passed:
        return skip(gate.reason or "perspective", gate=gate.as_dict(), mask=mask_meta, **plan.as_dict(rgb.shape[0]))

    progress(0.8, "composite")
    composite = compose(
        rgb, alpha, showroom.rgb, showroom.floor_top, cfg, plan=plan, light_wrap_strength=preset.adjustments.light_wrap
    )
    debug.rgb("background.jpg", composite.background)
    mark("composite")
    meta = {
        "applied": True,
        "experimental": True,
        "plate": cfg.plate,
        "gate": gate.as_dict(),
        "mask": mask_meta,
        **composite.info,
        "timingsMs": times,
    }
    return DetailOutcome(rgb=composite.rgb, applied=True, warnings=[], meta=meta)
