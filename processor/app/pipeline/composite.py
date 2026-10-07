"""STEPS 4/5 – cut the vehicle out and lay its ORIGINAL pixels over the showroom.

All blending happens in linear light with premultiplied alpha, so edges do not
get dark/bright fringes and fully opaque vehicle pixels are carried over 1:1
(only resampled to the target size).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .mask import BBox


@dataclass(frozen=True)
class VehicleLayer:
    #: Linear-light RGB (h, w, 3) float32 – NOT premultiplied.
    rgb: np.ndarray
    #: Alpha (h, w) float32 in [0, 1].
    alpha: np.ndarray
    #: Position of the layer's top-left corner relative to the vehicle bbox
    #: top-left, in layer pixels (negative = padding before the bbox).
    offset_x: float
    offset_y: float


def extract_vehicle(linear: np.ndarray, alpha: np.ndarray, bbox: BBox, pad_ratio: float = 0.02) -> VehicleLayer:
    """Crop the vehicle (plus a little padding for soft edges)."""
    height, width = alpha.shape
    pad = int(round(max(bbox.width, bbox.height) * pad_ratio)) + 2
    x0, y0 = max(bbox.x0 - pad, 0), max(bbox.y0 - pad, 0)
    x1, y1 = min(bbox.x1 + pad, width), min(bbox.y1 + pad, height)
    return VehicleLayer(
        rgb=np.ascontiguousarray(linear[y0:y1, x0:x1]),
        alpha=np.ascontiguousarray(alpha[y0:y1, x0:x1]),
        offset_x=float(x0 - bbox.x0),
        offset_y=float(y0 - bbox.y0),
    )


def resample_layer(layer: VehicleLayer, scale: float) -> VehicleLayer:
    """Uniform resize (aspect ratio preserved) using premultiplied alpha."""
    if abs(scale - 1.0) < 1e-6:
        return layer
    h, w = layer.alpha.shape
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LANCZOS4
    premultiplied = np.ascontiguousarray(layer.rgb * layer.alpha[..., None], dtype=np.float32)
    pm = cv2.resize(premultiplied, size, interpolation=interpolation)
    alpha = cv2.resize(layer.alpha, size, interpolation=interpolation)
    if scale > 1.0:
        # Lanczos over- and undershoots next to highlights/chrome; in linear light
        # that shows as dark rims that do not exist on the car. Keep every value
        # inside the range of its source neighbourhood (no invented detail).
        kernel = np.ones((3, 3), np.uint8)
        pm = np.clip(
            pm,
            cv2.resize(cv2.erode(premultiplied, kernel), size, interpolation=cv2.INTER_LINEAR),
            cv2.resize(cv2.dilate(premultiplied, kernel), size, interpolation=cv2.INTER_LINEAR),
        )
        alpha = np.clip(
            alpha,
            cv2.resize(cv2.erode(layer.alpha, kernel), size, interpolation=cv2.INTER_LINEAR),
            cv2.resize(cv2.dilate(layer.alpha, kernel), size, interpolation=cv2.INTER_LINEAR),
        )
    alpha = np.clip(alpha, 0.0, 1.0)
    pm = np.clip(pm, 0.0, None)
    rgb = np.where(alpha[..., None] > 1e-4, pm / np.maximum(alpha[..., None], 1e-4), 0.0)
    return VehicleLayer(
        rgb=np.clip(rgb, 0.0, 1.0).astype(np.float32),
        alpha=alpha.astype(np.float32),
        offset_x=layer.offset_x * scale,
        offset_y=layer.offset_y * scale,
    )


def feather_alpha(alpha: np.ndarray, sigma: float = 0.6) -> np.ndarray:
    """Sub-pixel softening of the cut line only (opaque interior unchanged)."""
    blurred = cv2.GaussianBlur(alpha, (0, 0), sigma)
    return np.where(alpha >= 0.999, np.maximum(alpha, blurred), blurred).astype(np.float32)


def feather_layer(layer: VehicleLayer, sigma: float = 0.6) -> VehicleLayer:
    """Feather the alpha and give pixels that become visible a matching colour.

    Pixels that were fully transparent carry no colour (black); when the
    feather makes them slightly visible they get the colour of the nearby
    vehicle edge instead of drawing a dark outline.
    """
    alpha = feather_alpha(layer.alpha, sigma)
    grown = (alpha > 1e-4) & (layer.alpha <= 1e-4)
    rgb = layer.rgb
    if grown.any():
        spread = max(1.5, 2.5 * sigma)
        pm = cv2.GaussianBlur(layer.rgb * layer.alpha[..., None], (0, 0), spread)
        weight = cv2.GaussianBlur(layer.alpha, (0, 0), spread)
        fill = pm / np.maximum(weight, 1e-4)[..., None]
        rgb = np.where(grown[..., None], np.clip(fill, 0.0, 1.0), layer.rgb).astype(np.float32)
    return VehicleLayer(rgb=rgb, alpha=alpha, offset_x=layer.offset_x, offset_y=layer.offset_y)


def place_layer(
    layer: VehicleLayer, bbox_left: float, bbox_top: float, width: int, height: int
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Return full-frame (rgb, alpha) with the layer at the placed position."""
    left = int(round(bbox_left + layer.offset_x))
    top = int(round(bbox_top + layer.offset_y))
    lh, lw = layer.alpha.shape
    rgb = np.zeros((height, width, 3), np.float32)
    alpha = np.zeros((height, width), np.float32)
    sx0, sy0 = max(0, -left), max(0, -top)
    dx0, dy0 = max(0, left), max(0, top)
    dx1, dy1 = min(width, left + lw), min(height, top + lh)
    if dx1 > dx0 and dy1 > dy0:
        rgb[dy0:dy1, dx0:dx1] = layer.rgb[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
        alpha[dy0:dy1, dx0:dx1] = layer.alpha[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
    return rgb, alpha, (left, top)


def over(background: np.ndarray, foreground: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = alpha[..., None]
    return (foreground * a + background * (1.0 - a)).astype(np.float32)
