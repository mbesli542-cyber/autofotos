"""The vehicle's reflection on the lacquered showroom floor.

The plate's floor reflects the LED strips, the wall-washer glow and the white
wall (the plate's reflection pass, ``<shot>-reflection.png``). A real car
standing on that floor blocks those reflections wherever the reflected viewing
ray hits the car – inside the car's mirror image and on the floor under the
car – and the floor shows the car's own reflection there instead. A car
without that looks pasted. :func:`apply_reflection` (after the grounding,
before the car is composited) does exactly that, in linear light:

- mirror axis per column = the ground model's floor line (the car footprint's
  near edge in perspective, through the tyre contacts) – no horizontal
  continuation that detaches the reflection from the bumpers;
- occlusion = the mirrored car silhouette plus the floor under the car and its
  mirror (the reflected underside): there the plate's own reflection is removed
  (as shadowed by the grounding) – no LED streak runs under the car;
- the mirrored ORIGINAL vehicle pixels (nothing regenerated) are added as a
  colour-neutral specular term with the floor's reflectance measured on the
  plate (reflection / mirrored wall radiance, plates.json ``reflectance``) ×
  ``reflection.strength`` – a white car shows a light mirrored bumper, the
  dark underside keeps the band under the car dark;
- the gloss blurs the reflection with the distance from the floor
  (``reflection.blurRate``, vertically VERTICAL_BLUR × as much), and the whole effect
  fades out between half of and ``reflection.fade`` × the vehicle height;
- floor pixels only (wall/floor junction from the plate); pixels fully covered
  by the car are left alone (the car is composited over them).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np

from ..presets import ReflectionConfig, ShowroomPlate
from .grounding import GroundModel, _fill_nearest, build_ground_model
from .placement import PlacementResult
from .shadow import floor_weight

#: Distances (px below the floor line, × vehicle height / 1000) of the blur levels.
BLUR_LEVELS = (0.0, 20.0, 50.0, 120.0, 280.0, 650.0)
#: The gloss lobe seen at a grazing angle stretches reflections towards the camera:
#: vertical blur = this × the horizontal blur.
VERTICAL_BLUR = 4.0
#: Minimum horizontal blur (× output width): the plate's LED streaks are ~15 px wide at 2400 px
#: even right at the wall – no razor-sharp vertical edge where the car's mirror image ends.
MIN_BLUR = 0.0025
#: The specular term never makes a floor pixel brighter than this (linear) – no glare spots.
MAX_LINEAR = 1.0


def _blur(image: np.ndarray, sigma_x: float, sigma_y: float) -> np.ndarray:
    """Anisotropic Gaussian blur; large sigmas run on a downsampled copy (same result within
    a fraction of a level, several times faster)."""
    if max(sigma_x, sigma_y) < 0.3:
        return image
    factor = 1 if sigma_x < 4.0 else (2 if sigma_x < 12.0 else 4)
    if factor == 1:
        return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma_x, sigmaY=sigma_y)
    h, w = image.shape[:2]
    small = cv2.resize(image, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=sigma_x / factor, sigmaY=sigma_y / factor)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _levels_weights(distance: np.ndarray, levels: np.ndarray) -> list[np.ndarray]:
    """Piecewise-linear weights of `distance` between consecutive `levels` (sum 1)."""
    d = np.clip(distance, levels[0], levels[-1])
    out = []
    for i, level in enumerate(levels):
        w = np.zeros_like(d)
        if i > 0:
            lo = levels[i - 1]
            w = np.where((d >= lo) & (d <= level), (d - lo) / max(level - lo, 1e-6), w)
        if i < len(levels) - 1:
            hi = levels[i + 1]
            w = np.where((d >= level) & (d < hi), (hi - d) / max(hi - level, 1e-6), w)
        if i == len(levels) - 1:
            w = np.where(d >= level, 1.0, w)
        out.append(w.astype(np.float32))
    return out


def plate_reflectance(plate: ShowroomPlate, cfg: ReflectionConfig, rows: np.ndarray) -> np.ndarray:
    """Reflectance applied to the car's mirror image per output row."""
    meta = plate.plate
    if meta.reflectance:
        k = meta.reflectance_at((rows + 0.5) / plate.height)
    else:
        k = np.full(len(rows), cfg.default_reflectance)
    return np.minimum(k * cfg.strength, cfg.max_reflectance).astype(np.float32)


def apply_reflection(
    floor_linear: np.ndarray,
    vehicle_rgb: np.ndarray,
    vehicle_alpha: np.ndarray,
    contacts_out: Sequence,
    plate: ShowroomPlate,
    placement: PlacementResult,
    cfg: ReflectionConfig,
    *,
    info: dict | None = None,
    model: GroundModel | None = None,
    background_linear: np.ndarray | None = None,
) -> np.ndarray:
    """Return the floor (LINEAR, H×W×3) with the vehicle's reflection (unchanged when disabled).

    `background_linear`: the plate before the grounding – the plate's own reflection is
    removed as far as the grounding left it (floor / background); without it the floor is
    assumed unshadowed there.
    """
    if not cfg.enabled or cfg.strength <= 0.0:
        if info is not None:
            info.update({"enabled": False})
        return floor_linear
    height, width = vehicle_alpha.shape
    if model is None:
        model = build_ground_model(vehicle_alpha, placement, contacts_out, plate)
    span = model.car_columns
    if span is None:
        return floor_linear
    first, last = span
    car_h = max(placement.height, 1.0)
    levels = np.array(BLUR_LEVELS) * car_h / 1000.0
    max_sigma = cfg.blur_rate * levels[-1]
    pad = int(math.ceil(3.0 * max_sigma)) + 2
    x0 = max(0, model.window.x0 + first - pad)
    x1 = min(width, model.window.x0 + last + 1 + pad)
    edge_all = _fill_nearest(model.edge)
    line_all = model.line_filled()
    sel = slice(x0 - model.window.x0, x1 - model.window.x0)
    edge, axis = edge_all[sel], line_all[sel]
    inside = np.zeros(x1 - x0, bool)
    inside[max(0, first - (x0 - model.window.x0)) : last + 1 - (x0 - model.window.x0)] = True
    # the car's ends: the under-car band fades out sideways (no vertical seam)
    beyond = np.maximum(np.maximum((model.window.x0 + first) - np.arange(x0, x1), np.arange(x0, x1) - (model.window.x0 + last)), 0)
    sideways = np.exp(-((beyond / max(2.0, 0.02 * placement.width)) ** 2)).astype(np.float32)
    behind = max(4.0, 0.025 * car_h)
    y0 = max(0, int(math.floor(np.min(edge[inside]) - behind)) - 2)
    y1 = height
    if y1 <= y0 or x1 <= x0:
        return floor_linear

    cols = np.arange(x0, x1, dtype=np.float32)
    rows = np.arange(y0, y1, dtype=np.float32)[:, None]
    centre = rows + 0.5
    distance = centre - axis[None, :].astype(np.float32)  # px below the floor line
    map_x = np.broadcast_to(cols[None, :], distance.shape).astype(np.float32)
    map_y = (2.0 * axis[None, :] - centre - 0.5).astype(np.float32)
    source = np.dstack([np.asarray(vehicle_rgb, np.float32) * vehicle_alpha[..., None], vehicle_alpha]).astype(np.float32)
    mirrored = cv2.remap(source, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    below = distance > 0.0
    mirrored *= below[..., None]
    # the floor under the car and its mirror image reflect the dark underside
    gap = (centre >= edge[None, :] - behind) & (centre <= 2.0 * axis[None, :] - edge[None, :])
    mirrored[..., 3] = np.maximum(mirrored[..., 3], gap.astype(np.float32) * sideways[None, :])

    # gloss: blur grows with the distance from the floor line
    weights = _levels_weights(np.maximum(distance, 0.0), levels)
    blurred = np.zeros_like(mirrored)
    for level, weight in zip(levels, weights):
        if not weight.any():
            continue
        sigma = cfg.blur_rate * level
        sigma_x = max(sigma, MIN_BLUR * width)  # the gloss lobe is never perfectly sharp sideways
        blurred += _blur(mirrored, sigma_x, VERTICAL_BLUR * sigma) * weight[..., None]
    spec, occlusion = blurred[..., :3], np.clip(blurred[..., 3], 0.0, 1.0)

    fade_px = max(4.0, cfg.fade * car_h)
    t = np.clip((distance - 0.5 * fade_px) / (0.5 * fade_px), 0.0, 1.0)
    fade = (1.0 - t * t * (3.0 - 2.0 * t)).astype(np.float32)
    k = plate_reflectance(plate, cfg, np.arange(y0, y1, dtype=np.float64))[:, None]

    region = np.asarray(floor_linear[y0:y1, x0:x1], np.float32)
    removed = np.zeros_like(region)
    if plate.reflection is not None:
        own = np.asarray(plate.reflection[y0:y1, x0:x1], np.float32)
        if background_linear is not None:
            base = np.asarray(background_linear[y0:y1, x0:x1], np.float32)
            shadowed = np.clip(region / np.maximum(base, 1e-5), 0.0, 1.0)
        else:
            shadowed = np.ones_like(region)
        removed = np.minimum(own * shadowed, region) * (occlusion * fade)[..., None]
    added = spec * (k * fade)[..., None]
    gate = floor_weight(plate.floor_top, height, width)[y0:y1, x0:x1]
    gate = gate * (np.asarray(vehicle_alpha[y0:y1, x0:x1]) < 1.0)
    delta = (added - removed) * gate[..., None]
    out = np.array(floor_linear, dtype=np.float32, copy=True)
    out[y0:y1, x0:x1] = np.minimum(region + delta, np.maximum(region, MAX_LINEAR))
    if info is not None:
        visible = gate > 0
        info.update(
            {
                "enabled": True,
                "strength": cfg.strength,
                "reflectance": [round(float(k.min()), 4), round(float(k.max()), 4)],
                "platePass": plate.reflection is not None,
                "fadePx": round(fade_px, 1),
                "blurRate": cfg.blur_rate,
                "window": [x0, y0, x1, y1],
                "maxStrength": round(float((k * fade).max()), 4),
                "removedMax": round(float(removed.max()) if removed.size else 0.0, 4),
                "addedMean": round(float(added[visible].mean()) if visible.any() else 0.0, 5),
            }
        )
    return out
