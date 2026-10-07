"""STEP 7 – deterministic, believable floor shadow derived from the vehicle mask.

1. Floor-contact line: the lower outline of the vehicle is analysed; its
   lowest support points (tyre contact patches) define where the floor is
   under every column – in 3/4 views the far wheels touch the floor higher in
   the image than the near wheels, so a single horizontal ground line is not
   enough.
2. Contact shadow: dark core + soft penumbra along the floor line, strongest
   where the vehicle actually touches the floor (tyres), fading for bumpers and
   sills that float above it.
3. Under-body occlusion: the floor directly below the car is darker, fading
   with the clearance height.
4. Ambient shadow: a broad, very soft band following the floor line, a little
   wider than the vehicle.

All deterministic image operations – no generative AI. The shadow is applied
to the background before the vehicle is composited, so it never covers the car.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..presets import ShadowConfig
from .color import srgb_to_linear
from .placement import PlacementResult

#: A floor line between two tyre contacts is never steeper than this (dy/dx).
#: Steeper hull segments end at a bumper/sill corner, not at a tyre.
MAX_FLOOR_SLOPE = 0.3
#: Slope limit for extending the floor line beyond the outermost contacts.
MAX_EXTRAPOLATION_SLOPE = 0.12
#: Darkness of the floor directly below the car body (no light reaches it).
UNDERBODY_OPACITY = 0.92


def bottom_profile(alpha: np.ndarray, x0: int, x1: int) -> tuple[np.ndarray, np.ndarray]:
    """Lowest solid vehicle pixel per column in [x0, x1)."""
    solid = alpha[:, x0:x1] >= 0.5
    has = solid.any(axis=0)
    flipped_first = np.argmax(solid[::-1, :], axis=0)
    bottom = (alpha.shape[0] - 1 - flipped_first).astype(np.float32)
    return bottom, has


def _floor_side_hull(xs: np.ndarray, ys: np.ndarray) -> list[tuple[float, float]]:
    """Convex hull chain on the floor side (max y in image coordinates)."""
    hull: list[tuple[float, float]] = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            # keep the chain "below" (larger y): remove points making a non-convex turn
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if cross >= 0:
                hull.pop()
            else:
                break
        hull.append((float(x), float(y)))
    return hull


def floor_contact_line(bottom: np.ndarray, has: np.ndarray, vehicle_height: float) -> np.ndarray:
    """Floor y per column, through the tyre contact points (piecewise linear)."""
    columns = np.flatnonzero(has)
    if len(columns) == 0:
        return np.full_like(bottom, float(bottom.max()))
    hull = _floor_side_hull(columns.astype(np.float32), bottom[columns])
    lowest = max(y for _, y in hull)
    contacts = [(x, y) for x, y in hull if y >= lowest - 0.15 * vehicle_height]
    # Bumper/sill corners can be hull vertices too: drop outer contacts that are
    # reached by a segment steeper than any floor line could be.
    def steep(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(b[1] - a[1]) > MAX_FLOOR_SLOPE * max(abs(b[0] - a[0]), 1.0)

    while len(contacts) > 1 and steep(contacts[0], contacts[1]):
        contacts.pop(0 if contacts[0][1] < contacts[1][1] else 1)
    while len(contacts) > 1 and steep(contacts[-2], contacts[-1]):
        contacts.pop(-1 if contacts[-1][1] < contacts[-2][1] else -2)
    xs = np.arange(len(bottom), dtype=np.float32)
    if len(contacts) == 1:
        return np.full_like(bottom, contacts[0][1])
    cx = np.array([c[0] for c in contacts], np.float32)
    cy = np.array([c[1] for c in contacts], np.float32)
    line = np.interp(xs, cx, cy)
    # gentle linear extrapolation beyond the outermost contacts (floor perspective)
    lim = MAX_EXTRAPOLATION_SLOPE
    left_slope = float(np.clip((cy[1] - cy[0]) / max(cx[1] - cx[0], 1.0), -lim, lim))
    right_slope = float(np.clip((cy[-1] - cy[-2]) / max(cx[-1] - cx[-2], 1.0), -lim, lim))
    line = np.where(xs < cx[0], cy[0] + (xs - cx[0]) * left_slope, line)
    line = np.where(xs > cx[-1], cy[-1] + (xs - cx[-1]) * right_slope, line)
    return line.astype(np.float32)


def build_shadow(
    alpha: np.ndarray, placement: PlacementResult, cfg: ShadowConfig, floor_y: float
) -> np.ndarray:
    height, width = alpha.shape
    vw = max(placement.width, 1.0)
    vh = max(placement.height, 1.0)
    extend = int(round(0.06 * vw))
    x0 = max(0, int(np.floor(placement.left)) - extend)
    x1 = min(width, int(np.ceil(placement.left + placement.width)) + extend)

    bottom, has = bottom_profile(alpha, x0, x1)
    floor_line = floor_contact_line(bottom, has, vh)
    clearance = np.where(has, np.clip(floor_line - bottom, 0.0, None), 1e6)
    ys = np.arange(height, dtype=np.float32)[:, None]
    d = ys - floor_line[None, :]  # > 0: below the floor line (towards the camera)

    # Contact: dark core + penumbra, strongest where clearance ~ 0 (tyres).
    touch = np.exp(-clearance / (0.012 * vh))
    near = np.exp(-clearance / (0.05 * vh))
    core_sigma = max(1.5, 0.5 * cfg.contact_height * vw)
    pen_sigma = max(2.0, cfg.contact_height * vw)
    core = np.exp(-((d / core_sigma) ** 2)) * touch[None, :]
    penumbra = np.where(d >= 0, np.exp(-((d / pen_sigma) ** 2)), np.exp(-((d / (0.6 * pen_sigma)) ** 2)))
    penumbra = penumbra * near[None, :]
    contact = np.zeros((height, width), np.float32)
    contact[:, x0:x1] = np.clip(1.0 * core + 0.6 * penumbra, 0.0, 1.0)
    contact = cv2.GaussianBlur(contact, (0, 0), sigmaX=max(1.0, 0.003 * vw), sigmaY=0.8)

    # Under-body occlusion: between the car's underside and the floor line.
    occlusion_strength = np.exp(-clearance / (0.45 * vh)) * has
    under = np.zeros((height, width), np.float32)
    region = (ys >= bottom[None, :]) & (ys <= floor_line[None, :] + 0.25 * pen_sigma)
    under[:, x0:x1] = region * occlusion_strength[None, :]
    under = cv2.GaussianBlur(under, (0, 0), sigmaX=max(2.0, 0.02 * vw), sigmaY=max(1.5, 0.008 * vw))

    # Ambient: soft band along the floor line, slightly wider than the vehicle.
    ambient = np.zeros((height, width), np.float32)
    band_up = 0.5 * cfg.ambient_height * vw
    band_down = 0.35 * cfg.ambient_height * vw
    footprint = (d >= -band_up) & (d <= band_down)
    span = np.zeros(x1 - x0, np.float32)
    inner_left = int(np.floor(placement.left)) - x0
    inner_right = int(np.ceil(placement.left + placement.width)) - x0
    grow = 0.5 * vw * (cfg.ambient_spread - 1.0)
    cols = np.arange(x1 - x0, dtype=np.float32)
    span = np.clip(1.0 - np.maximum(inner_left - cols, cols - inner_right) / max(grow + 1.0, 1.0), 0.0, 1.0)
    span = np.where((cols >= inner_left) & (cols <= inner_right), 1.0, span)
    ambient[:, x0:x1] = footprint * span[None, :]
    ambient = cv2.GaussianBlur(ambient, (0, 0), max(2.0, cfg.ambient_blur * vw))

    shadow = 1.0 - (
        (1.0 - np.clip(cfg.contact_opacity, 0, 1) * contact)
        * (1.0 - UNDERBODY_OPACITY * under)
        * (1.0 - np.clip(cfg.ambient_opacity, 0, 1) * ambient)
    )
    shadow[: max(0, int(floor_y))] = 0.0  # shadows only fall on the floor
    return np.clip(shadow, 0.0, 1.0).astype(np.float32)


def apply_shadow(background_linear: np.ndarray, shadow: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    """Darken the floor towards the (warm, dark) shadow colour – never pure black."""
    tint = srgb_to_linear(np.asarray(color, np.uint8).reshape(1, 1, 3))
    darkest = np.minimum(background_linear, tint)
    s = shadow[..., None]
    return (background_linear * (1.0 - s) + darkest * s).astype(np.float32)
