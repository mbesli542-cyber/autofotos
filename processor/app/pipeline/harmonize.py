"""STEP 8 – edge harmonisation so the cut-out does not look pasted.

Only the outermost edge band of the vehicle is touched:
- decontamination: semi-transparent edge pixels still contain the OLD
  background colour; they get the colour of the neighbouring opaque vehicle
  pixels instead (weighted by transparency),
- light wrap: a few percent of the blurred showroom light bleeds over the
  outermost 1–3 px, like in a real photo.
The opaque body of the vehicle is never changed here.
"""

from __future__ import annotations

import cv2
import numpy as np


def decontaminate_edges(rgb: np.ndarray, alpha: np.ndarray, radius: float = 3.0) -> np.ndarray:
    core = (alpha >= 0.97).astype(np.float32)
    sigma = max(1.0, float(radius))
    numerator = cv2.GaussianBlur(rgb * core[..., None], (0, 0), sigma)
    denominator = cv2.GaussianBlur(core, (0, 0), sigma)
    estimate = np.where(
        denominator[..., None] > 1e-3, numerator / np.maximum(denominator[..., None], 1e-3), rgb
    )
    edge = (alpha < 0.97) & (alpha > 0.0)
    weight = np.where(edge, 1.0 - alpha, 0.0)[..., None].astype(np.float32)
    return (rgb * (1.0 - weight) + estimate * weight).astype(np.float32)


def light_wrap(
    rgb: np.ndarray, alpha: np.ndarray, background: np.ndarray, strength: float, width_px: float
) -> np.ndarray:
    if strength <= 0:
        return rgb
    sigma = max(1.0, float(width_px))
    inner = cv2.GaussianBlur(alpha, (0, 0), sigma)
    edge = np.clip(alpha - inner, 0.0, 1.0) * 2.0  # only near the outline
    edge = np.clip(edge, 0.0, 1.0) * (alpha > 0.0)
    ambient = cv2.GaussianBlur(background, (0, 0), sigma * 3.0)
    weight = (strength * edge)[..., None].astype(np.float32)
    return (rgb * (1.0 - weight) + ambient * weight).astype(np.float32)
