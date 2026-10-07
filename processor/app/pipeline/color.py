"""Colour-space helpers (sRGB ⇄ linear light, Lab, hue/saturation)."""

from __future__ import annotations

import cv2
import numpy as np


def srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    """uint8 or float [0,1] sRGB → float32 linear light."""
    x = srgb.astype(np.float32)
    if srgb.dtype == np.uint8:
        x /= 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """float32 linear light → float32 sRGB [0,1]."""
    x = np.clip(linear, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


def linear_to_u8(linear: np.ndarray) -> np.ndarray:
    return np.clip(np.round(linear_to_srgb(linear) * 255.0), 0, 255).astype(np.uint8)


def luminance(linear: np.ndarray) -> np.ndarray:
    return linear[..., 0] * 0.2126 + linear[..., 1] * 0.7152 + linear[..., 2] * 0.0722


def lab_from_linear(linear: np.ndarray) -> np.ndarray:
    """Linear RGB (…,3) → CIE Lab (OpenCV float convention: L 0..100)."""
    srgb = linear_to_srgb(linear).astype(np.float32)
    flat = srgb.reshape(-1, 1, 3)
    lab = cv2.cvtColor(flat, cv2.COLOR_RGB2Lab)
    return lab.reshape(linear.shape)


def hue_degrees(lab: np.ndarray) -> np.ndarray:
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))


def chroma(lab: np.ndarray) -> np.ndarray:
    return np.hypot(lab[..., 1], lab[..., 2])


def linear_saturation(linear: np.ndarray) -> np.ndarray:
    """(max-min)/max in LINEAR light – exactly invariant to exposure gain."""
    mx = linear.max(axis=-1)
    mn = linear.min(axis=-1)
    return np.where(mx > 1e-5, (mx - mn) / np.maximum(mx, 1e-5), 0.0)
