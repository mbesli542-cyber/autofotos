"""Shared fixtures: synthetic vehicle photos with known masks, fake segmenter,
isolated asset/data directories. No model download needed for the tests."""

from __future__ import annotations

import io
import json
import shutil
import threading
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import REPO_ROOT, Settings

NAVY = (24, 34, 78)  # dark navy blue paint (sRGB)


def make_vehicle_photo(width: int = 2000, height: int = 1500, *, seed: int = 3):
    """Synthetic 'car' (navy body, glass, black tyres, mirror) on a busy outdoor background.

    Returns (rgb uint8, ground-truth mask float32 [0,1]).
    """
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    sky = np.array([170, 190, 210], np.float32)
    ground = np.array([95, 98, 92], np.float32)
    background = np.where(y < 0.55, sky, ground) + rng.normal(0, 9, (height, width, 3)).astype(np.float32)
    rgb = np.clip(background, 0, 255).astype(np.uint8)

    mask = np.zeros((height, width), np.uint8)
    cx, base = width // 2, int(height * 0.78)
    body_w, body_h = int(width * 0.5), int(height * 0.16)
    x0, x1 = cx - body_w // 2, cx + body_w // 2
    # body
    cv2.rectangle(mask, (x0, base - body_h), (x1, base - int(0.03 * height)), 255, -1)
    # cabin (trapezoid)
    roof = np.array(
        [[x0 + int(0.2 * body_w), base - body_h], [x0 + int(0.32 * body_w), base - int(1.75 * body_h)],
         [x0 + int(0.7 * body_w), base - int(1.75 * body_h)], [x0 + int(0.82 * body_w), base - body_h]],
        np.int32,
    )
    cv2.fillPoly(mask, [roof], 255)
    # wheels
    radius = int(0.075 * height)
    for wx in (x0 + int(0.2 * body_w), x1 - int(0.2 * body_w)):
        cv2.circle(mask, (wx, base - radius), radius, 255, -1)
    # side mirror (separate small blob, attached visually)
    cv2.ellipse(mask, (x0 + int(0.25 * body_w), base - int(1.08 * body_h)), (18, 10), 0, 0, 360, 255, -1)

    paint = np.array(NAVY, np.uint8)
    rgb[mask > 0] = paint
    # glass (inside the cabin) and tyres
    glass = np.zeros_like(mask)
    inner = roof.copy()
    inner[:, 1] += np.array([-6, 14, 14, -6])
    inner[:, 0] += np.array([22, 12, -12, -22])
    cv2.fillPoly(glass, [inner], 255)
    rgb[glass > 0] = (60, 70, 80)
    tyres = np.zeros_like(mask)
    for wx in (x0 + int(0.2 * body_w), x1 - int(0.2 * body_w)):
        cv2.circle(tyres, (wx, base - radius), radius, 255, -1)
    rgb[tyres > 0] = (22, 22, 24)
    return rgb, (mask.astype(np.float32) / 255.0)


def encode_jpeg(rgb: np.ndarray, quality: int = 95, exif=None) -> bytes:
    buffer = io.BytesIO()
    image = Image.fromarray(rgb)
    if exif is not None:
        image.save(buffer, "JPEG", quality=quality, exif=exif)
    else:
        image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


class FakeSegmenter:
    """Returns the registered ground-truth mask for an image size (soft edges)."""

    name = "fake"

    def __init__(self, masks: dict[tuple[int, int], np.ndarray] | None = None):
        self.masks = dict(masks or {})
        self.calls = 0
        self.gate: threading.Event | None = None

    def register(self, mask: np.ndarray) -> None:
        self.masks[mask.shape] = mask

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(timeout=10)
        mask = self.masks.get(rgb.shape[:2])
        if mask is None:
            from app.pipeline.segmentation import SegmentationError

            raise SegmentationError("no mask registered")
        return cv2.GaussianBlur(mask, (0, 0), 1.2)


@pytest.fixture
def vehicle():
    return make_vehicle_photo()


@pytest.fixture
def fake_segmenter(vehicle):
    seg = FakeSegmenter()
    seg.register(vehicle[1])
    return seg


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    """Copy of the real preset + logo, with a small deterministic master background."""
    assets = tmp_path / "assets"
    (assets / "presets").mkdir(parents=True)
    (assets / "brand" / "official").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "public/presets/autoexperten-standard.json", assets / "presets")
    shutil.copy(
        REPO_ROOT / "public/brand/official/AutoExperten_Logo.png", assets / "brand/official/AutoExperten_Logo.png"
    )
    preset = json.loads((assets / "presets/autoexperten-standard.json").read_text())
    background = np.zeros((1200, 1600, 3), np.uint8)
    junction = int(round(preset["background"]["floorHorizon"] * 1200))
    background[:junction] = (236, 237, 240)  # wall
    background[junction:] = (156, 112, 74)  # wooden floor tone
    Image.fromarray(background).save(assets / "presets" / preset["background"]["image"], quality=95)
    return assets


@pytest.fixture
def settings(tmp_path: Path, assets_dir: Path) -> Settings:
    return Settings(
        assets_dir=assets_dir,
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        auto_download_models=False,
        debug=False,
    )


@pytest.fixture
def debug_settings(settings: Settings) -> Settings:
    return replace(settings, debug=True)
