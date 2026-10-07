"""STEP 3 – turn the coarse model output into a clean, high-resolution alpha.

1. Edge refinement at full resolution with a guided filter (the photo itself
   guides where the true edge is), applied only in the uncertain edge band.
2. Keep the vehicle: the largest component plus attached parts (mirrors,
   antennas) – drop unrelated blobs (people, other cars far away).
3. Fill enclosed holes in the UPPER part of the vehicle (windows/glass roofs
   that the model treated as see-through). Holes near the ground (the gap
   under the car between the wheels) are left open.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .segmentation import SegmentationError


@dataclass(frozen=True)
class BBox:
    x0: int
    y0: int
    x1: int  # exclusive
    y1: int  # exclusive

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def aspect(self) -> float:
        return self.width / max(self.height, 1)


@dataclass
class MaskInfo:
    components_total: int = 0
    components_kept: int = 0
    holes_filled: int = 0
    hole_area_filled: int = 0
    uncertain_fraction: float = 0.0
    coverage: float = 0.0


@dataclass(frozen=True)
class QualityWarning:
    code: str
    message: str


def _box(image: np.ndarray, radius: int) -> np.ndarray:
    size = 2 * radius + 1
    return cv2.boxFilter(image, -1, (size, size), normalize=True, borderType=cv2.BORDER_REFLECT)


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """Classic grey-guide guided filter (He et al.), float32 in/out."""
    mean_i = _box(guide, radius)
    mean_p = _box(src, radius)
    corr_ii = _box(guide * guide, radius)
    corr_ip = _box(guide * src, radius)
    var_i = corr_ii - mean_i * mean_i
    cov_ip = corr_ip - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return _box(a, radius) * guide + _box(b, radius)


def refine_alpha(rgb: np.ndarray, coarse: np.ndarray, model_input_size: int = 1024) -> np.ndarray:
    """Sharpen the up-sampled model mask along real image edges."""
    if coarse.shape != rgb.shape[:2]:
        raise ValueError("mask and image size differ")
    height, width = coarse.shape
    upscale = max(height, width) / float(model_input_size)
    radius = int(np.clip(round(2.0 * upscale), 2, 24))
    guide = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    refined = guided_filter(guide, coarse.astype(np.float32), radius, eps=1e-3)
    # Only trust the refinement inside the uncertain edge band.
    uncertain = ((coarse > 0.02) & (coarse < 0.98)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    band = cv2.dilate(uncertain, kernel).astype(bool)
    alpha = np.where(band, refined, coarse)
    # Mild choke: removes the faint halo that carries old background colour.
    alpha = (alpha - 0.04) / 0.92
    return np.clip(alpha, 0.0, 1.0).astype(np.float32)


#: Enclosed holes lower than this (relative to the vehicle height) stay open.
MIN_FILL_HOLE_HEIGHT = 0.08


def clean_mask(alpha: np.ndarray, *, upper_fill_ratio: float = 0.62) -> tuple[np.ndarray, MaskInfo]:
    info = MaskInfo()
    binary = (alpha >= 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    info.components_total = max(count - 1, 0)
    if count <= 1:
        raise SegmentationError("no vehicle found in the photo")

    areas = stats[1:, cv2.CC_STAT_AREA]
    main = int(np.argmax(areas)) + 1
    main_area = int(stats[main, cv2.CC_STAT_AREA])
    mx, my, mw, mh = (int(v) for v in stats[main, :4])
    pad_x, pad_y = int(0.06 * mw), int(0.06 * mh)
    ex0, ey0, ex1, ey1 = mx - pad_x, my - pad_y, mx + mw + pad_x, my + mh + pad_y

    keep = [main]
    for label in range(1, count):
        if label == main:
            continue
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 0.002 * main_area:
            continue
        x, y, w, h = (int(v) for v in stats[label, :4])
        ix = max(0, min(x + w, ex1) - max(x, ex0))
        iy = max(0, min(y + h, ey1) - max(y, ey0))
        # Attached parts (mirrors, antennas) lie inside the vehicle's box.
        if ix * iy >= 0.8 * w * h:
            keep.append(label)
    info.components_kept = len(keep)
    keep_mask = np.isin(labels, keep).astype(np.uint8)

    # Enclosed holes (background components not touching the border).
    ys, xs = np.nonzero(keep_mask)
    top, bottom = int(ys.min()), int(ys.max())
    vehicle_area = int(keep_mask.sum())
    inverse = (1 - keep_mask).astype(np.uint8)
    hcount, hlabels, hstats, hcent = cv2.connectedComponentsWithStats(inverse, connectivity=4)
    filled = np.zeros_like(keep_mask)
    height, width = keep_mask.shape
    limit_y = top + upper_fill_ratio * (bottom - top)
    for label in range(1, hcount):
        x, y, w, h, area = (int(v) for v in hstats[label])
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            continue  # connected to the outside → real background
        if area > 0.35 * vehicle_area:
            continue
        if hcent[label][1] > limit_y:
            continue  # near the ground: gap under the car, keep it open
        if h < MIN_FILL_HOLE_HEIGHT * (bottom - top) or area < 0.25 * w * h:
            # thin gaps (under roof rails, spoilers, roof boxes) show real
            # background – only window-sized holes are glass the model missed
            continue
        filled[hlabels == label] = 1
        info.holes_filled += 1
        info.hole_area_filled += area

    gate = cv2.dilate(keep_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    result = alpha * gate.astype(np.float32)
    result = np.maximum(result, filled.astype(np.float32))
    vehicle = result >= 0.5
    total = int(vehicle.sum())
    info.coverage = total / float(result.size)
    soft = (result > 0.15) & (result < 0.85)
    info.uncertain_fraction = float(soft.sum()) / max(total, 1)
    return result.astype(np.float32), info


def mask_bbox(alpha: np.ndarray, threshold: float = 0.5) -> BBox:
    ys, xs = np.nonzero(alpha >= threshold)
    if len(xs) == 0:
        raise SegmentationError("empty vehicle mask")
    return BBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


SIDE_LABELS = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}


def assess_mask(alpha: np.ndarray, bbox: BBox, info: MaskInfo) -> list[QualityWarning]:
    height, width = alpha.shape
    warnings: list[QualityWarning] = []
    margin_x, margin_y = max(2, int(0.004 * width)), max(2, int(0.004 * height))
    sides = []
    if bbox.x0 <= margin_x:
        sides.append("left")
    if bbox.x1 >= width - margin_x:
        sides.append("right")
    if bbox.y0 <= margin_y:
        sides.append("top")
    if bbox.y1 >= height - margin_y:
        sides.append("bottom")
    if sides:
        warnings.append(
            QualityWarning(
                "vehicle_cropped",
                "Das Fahrzeug ist im Originalfoto angeschnitten ("
                + ", ".join(SIDE_LABELS[s] for s in sides)
                + "). Bitte das ganze Fahrzeug fotografieren.",
            )
        )
    if info.coverage < 0.02:
        warnings.append(
            QualityWarning("vehicle_small", "Das Fahrzeug ist im Foto sehr klein – bitte näher herangehen.")
        )
    if info.uncertain_fraction > 0.12:
        warnings.append(
            QualityWarning("mask_uncertain", "Die Freistellung ist unsicher – bitte das Ergebnis prüfen.")
        )
    if info.components_total > 25:
        warnings.append(
            QualityWarning(
                "busy_background",
                "Unruhiger Hintergrund erkannt – bitte Kanten des Fahrzeugs prüfen.",
            )
        )
    return warnings
