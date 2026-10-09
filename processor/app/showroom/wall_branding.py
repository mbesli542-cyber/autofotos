"""Official branding on the showroom wall, in WALL SPACE (per plate perspective).

The branding layout (app/showroom/branding.py, positions in the preset's
``branding`` section) is rendered onto a uniform canvas that represents the
plate's ``wall.brandArea`` – fractions are of that wall rectangle: x from
``xMin`` (0) to ``xMax`` (1), ``top`` 0 at ``zMax`` and 1 at ``zMin``.

The canvas is the wall albedo; the branded canvas divided by it gives a per
pixel, per channel ratio in LINEAR light. Multiplying the plate by that ratio
is the physically correct relighting of a matte print on the wall: the logo
receives exactly the light the plate's wall receives there (spot pools,
falloff), its pixels/colours are only scaled by the official PNG – never
redrawn. The ratio is warped into the plate with the plate's
``wallHomography``; pixels without branding keep their exact plate values.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .branding import BrandingConfig, BrandingLayout, brand_uniform_band, logo_stamp
from .plates import Plate

log = logging.getLogger(__name__)

#: Canvas pixels per output pixel at the place where the wall is closest (largest).
SUPERSAMPLE = 2.0
#: Canvas density is rounded up to a multiple of this (px per metre) – shared cache entries.
DENSITY_STEP = 32.0
#: Never render canvases wider than this (memory guard).
MAX_CANVAS_WIDTH = 9000
#: Ratio deviations below this are treated as "no branding" (pixel copied bit-exact).
UNCHANGED_EPS = 1e-4


@dataclass(frozen=True)
class _CanvasRatio:
    #: LINEAR ratio branded/plain (h, w, 3) for the crop that contains the branding.
    ratio: np.ndarray
    #: Crop position in the full canvas (px).
    x0: int
    y0: int
    #: Element boxes in full-canvas px: (name, x0, y0, x1, y1).
    boxes: tuple[tuple[str, int, int, int, int], ...]


def wall_to_output(plate: Plate, width: int, height: int) -> np.ndarray:
    """3x3: wall (x, z, 1) metres → output pixel coordinates (OpenCV centres)."""
    to_px = np.array([[width, 0.0, -0.5], [0.0, height, -0.5], [0.0, 0.0, 1.0]])
    return to_px @ plate.wall_h


def _canvas_to_wall(plate: Plate, density: float) -> np.ndarray:
    """3x3: canvas pixel (OpenCV centres) → wall metres (x, z)."""
    area = plate.brand_area
    return np.array(
        [
            [1.0 / density, 0.0, area.x_min + 0.5 / density],
            [0.0, -1.0 / density, area.z_max - 0.5 / density],
            [0.0, 0.0, 1.0],
        ]
    )


def output_density(plate: Plate, width: int, height: int) -> float:
    """Largest output px per wall metre over the visible brand area."""
    m = wall_to_output(plate, width, height)
    area = plate.brand_area
    xs = np.linspace(area.x_min, area.x_max, 13)
    zs = np.linspace(area.z_min, area.z_max, 9)
    best = 0.0
    eps = 1e-3
    for x in xs:
        for z in zs:
            p = _project(m, x, z)
            if p is None or not (-0.1 * width <= p[0] <= 1.1 * width and -0.1 * height <= p[1] <= 1.1 * height):
                continue
            px, pz = _project(m, x + eps, z), _project(m, x, z + eps)
            if px is None or pz is None:
                continue
            jac = np.array([[px[0] - p[0], pz[0] - p[0]], [px[1] - p[1], pz[1] - p[1]]]) / eps
            best = max(best, float(np.linalg.svd(jac, compute_uv=False)[0]))
    return best


def _project(m: np.ndarray, x: float, z: float) -> tuple[float, float] | None:
    q = m @ np.array([x, z, 1.0])
    if q[2] <= 1e-9:
        return None
    return float(q[0] / q[2]), float(q[1] / q[2])


@lru_cache(maxsize=4)
def _canvas_ratio(
    cfg: BrandingConfig, brand_dir: str, _logo_stamp: int, width: int, height: int, albedo: tuple[float, float, float]
) -> _CanvasRatio | None:
    # only the band of rows with branding is rendered (the canvas can be ~5000 px wide)
    rendered = brand_uniform_band(albedo, width, height, cfg, Path(brand_dir))
    if rendered is None or not rendered[1].boxes:
        return None
    ratio, layout = rendered
    ratio /= np.asarray(albedo, np.float32)  # in place: branded / plain
    changed = (np.abs(ratio - 1.0) > UNCHANGED_EPS).any(axis=-1)
    ys, xs = np.nonzero(changed)
    if len(ys) == 0:
        return None
    pad = 2
    y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + 1 + pad, height)
    x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + 1 + pad, width)
    crop = np.ascontiguousarray(ratio[y0:y1, x0:x1])
    crop.setflags(write=False)
    return _CanvasRatio(ratio=crop, x0=x0, y0=y0, boxes=layout.boxes)


def canvas_size(plate: Plate, width: int, height: int) -> tuple[int, int, float]:
    """(canvas width, canvas height, px per metre) for a plate at an output size."""
    density = SUPERSAMPLE * max(output_density(plate, width, height), 1.0)
    density = math.ceil(density / DENSITY_STEP) * DENSITY_STEP
    area = plate.brand_area
    if area.width * density > MAX_CANVAS_WIDTH:
        density = math.floor(MAX_CANVAS_WIDTH / area.width)
    return int(math.ceil(area.width * density)), int(math.ceil(area.height * density)), float(density)


def apply_wall_branding(
    rgb: np.ndarray, plate: Plate, cfg: BrandingConfig, brand_dir: Path
) -> tuple[np.ndarray, BrandingLayout]:
    """Brand the plate image (sRGB uint8, already at output size) through its wall homography.

    Returns the branded copy and the element boxes projected into output pixels
    (clipped to the frame; elements outside the frame are omitted).
    """
    height, width = rgb.shape[:2]
    if not cfg.enabled:
        return rgb, BrandingLayout(boxes=())
    cw, ch, density = canvas_size(plate, width, height)
    canvas = _canvas_ratio(cfg, str(brand_dir), logo_stamp(brand_dir / cfg.logo.file), cw, ch, plate.wall_albedo)
    if canvas is None:
        return rgb, BrandingLayout(boxes=())
    canvas_to_out = wall_to_output(plate, width, height) @ _canvas_to_wall(plate, density)

    # output region covered by the branding crop
    crop_h, crop_w = canvas.ratio.shape[:2]
    corners = [
        _project(canvas_to_out, canvas.x0 + dx, canvas.y0 + dy)
        for dx in (-0.5, crop_w - 0.5)
        for dy in (-0.5, crop_h - 0.5)
    ]
    if any(c is None for c in corners):
        log.warning("Plate %s: the brand area is not in front of the camera – branding skipped", plate.key)
        return rgb, BrandingLayout(boxes=())
    rx0 = max(int(math.floor(min(c[0] for c in corners))) - 2, 0)
    ry0 = max(int(math.floor(min(c[1] for c in corners))) - 2, 0)
    rx1 = min(int(math.ceil(max(c[0] for c in corners))) + 3, width)
    ry1 = min(int(math.ceil(max(c[1] for c in corners))) + 3, height)
    if rx1 <= rx0 or ry1 <= ry0:
        return rgb, BrandingLayout(boxes=())

    # warp into a 2x supersampled region, then area-average (anti-aliased, never upscaled)
    ss = 2
    crop_offset = np.array([[1.0, 0.0, canvas.x0], [0.0, 1.0, canvas.y0], [0.0, 0.0, 1.0]])
    to_ss = np.array([[ss, 0.0, (ss - 1) / 2.0 - ss * rx0], [0.0, ss, (ss - 1) / 2.0 - ss * ry0], [0.0, 0.0, 1.0]])
    matrix = to_ss @ canvas_to_out @ crop_offset
    rw, rh = rx1 - rx0, ry1 - ry0
    warped = cv2.warpPerspective(
        np.asarray(canvas.ratio),
        matrix,
        (rw * ss, rh * ss),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(1.0, 1.0, 1.0),
    )
    ratio = cv2.resize(warped, (rw, rh), interpolation=cv2.INTER_AREA)

    region = rgb[ry0:ry1, rx0:rx1]
    changed = np.abs(ratio - 1.0).max(axis=-1) > UNCHANGED_EPS
    out = rgb.copy()
    if changed.any():
        linear = _srgb_to_linear(region[changed].astype(np.float32) / 255.0)
        lit = linear * ratio[changed]
        # a print can never be brighter than white: scale the pixel, never clip channels (hue stays)
        peak = lit.max(axis=-1, keepdims=True)
        lit = np.where(peak > 1.0, lit / np.maximum(peak, 1e-6), lit)
        patch = region.copy()
        patch[changed] = _linear_to_srgb_u8(lit)
        out[ry0:ry1, rx0:rx1] = patch

    boxes = []
    for name, bx0, by0, bx1, by1 in canvas.boxes:
        pts = [_project(canvas_to_out, x, y) for x in (bx0 - 0.5, bx1 - 0.5) for y in (by0 - 0.5, by1 - 0.5)]
        if any(p is None for p in pts):
            continue
        x0 = max(int(math.floor(min(p[0] for p in pts) + 0.5)), 0)
        y0 = max(int(math.floor(min(p[1] for p in pts) + 0.5)), 0)
        x1 = min(int(math.ceil(max(p[0] for p in pts) + 0.5)), width)
        y1 = min(int(math.ceil(max(p[1] for p in pts) + 0.5)), height)
        if x1 > x0 and y1 > y0:
            boxes.append((name, x0, y0, x1, y1))
    return out, BrandingLayout(boxes=tuple(boxes))


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _linear_to_srgb_u8(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return np.clip(s * 255.0 + 0.5, 0, 255).astype(np.uint8)
