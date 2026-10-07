"""Tests for the procedural AutoExperten Standard placeholder showroom."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.showroom import placeholder
from app.showroom.placeholder import ShowroomBrandText, render_placeholder_showroom

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGO = REPO_ROOT / "public" / "brand" / "official" / "AutoExperten_Logo.png"
BRAND_BLUE = np.array([7, 136, 234], np.float32)  # #0788EA


@lru_cache(maxsize=4)
def _render(width: int, height: int, seed: int = 7) -> np.ndarray:
    image = render_placeholder_showroom(width, height, logo_path=LOGO, brand=ShowroomBrandText(), seed=seed)
    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (width, height)
    array = np.asarray(image)
    array.setflags(write=False)
    return array


def _region(a: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    h, w = a.shape[:2]
    return a[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)].astype(np.float32)


def _luma(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


@pytest.mark.parametrize("size", [(2400, 1800), (3200, 2400)])
def test_exact_size_and_mode(size: tuple[int, int]) -> None:
    a = _render(*size)
    assert a.shape == (size[1], size[0], 3)
    assert a.dtype == np.uint8
    # a real picture, not a flat fill
    assert 120 < a.mean() < 230
    assert a.std() > 20


def test_deterministic_pixels() -> None:
    first = _render(2400, 1800)
    again = np.asarray(
        render_placeholder_showroom(2400, 1800, logo_path=LOGO, brand=ShowroomBrandText(), seed=7)
    )
    assert np.array_equal(first, again)


def test_seed_changes_texture_only() -> None:
    a = np.asarray(render_placeholder_showroom(480, 360, logo_path=LOGO, brand=ShowroomBrandText(), seed=7))
    b = np.asarray(render_placeholder_showroom(480, 360, logo_path=LOGO, brand=ShowroomBrandText(), seed=8))
    assert not np.array_equal(a, b)
    # same composition: overall brightness nearly identical
    assert abs(float(a.mean()) - float(b.mean())) < 3.0


def test_small_and_odd_sizes() -> None:
    for w, h in [(64, 64), (321, 239), (1920, 1080), (900, 1200)]:
        image = render_placeholder_showroom(w, h, logo_path=LOGO, brand=ShowroomBrandText())
        assert image.size == (w, h)
        assert image.mode == "RGB"


@pytest.mark.parametrize("size", [(2400, 1800), (3200, 2400)])
def test_central_floor_is_uncluttered(size: tuple[int, int]) -> None:
    a = _render(*size)
    floor = _luma(_region(a, 0.30, 0.70, 0.70, 0.95))
    side = _luma(_region(a, 0.00, 0.30, 0.08, 0.65))  # slat panel + plant
    assert floor.std() < 22
    assert floor.std() < 0.6 * side.std()
    # wall behind the vehicle: only smooth light, no objects or text
    wall = _luma(_region(a, 0.14, 0.30, 0.86, 0.60))
    assert wall.std() < 12
    assert wall.min() > 150


@pytest.mark.parametrize("size", [(2400, 1800), (3200, 2400)])
def test_vehicle_zone_contains_no_plants(size: tuple[int, int]) -> None:
    a = _render(*size).astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = (g > r + 12) & (g > b + 12)
    h, w = green.shape
    zone = green[int(0.28 * h) : int(0.97 * h), int(0.08 * w) : int(0.92 * w)]
    assert zone.sum() == 0
    # ...but there are plants in the outer side areas
    assert green[:, : int(0.08 * w)].sum() > 500
    assert green[:, int(0.92 * w) :].sum() > 500


@pytest.mark.parametrize("size", [(2400, 1800), (3200, 2400)])
def test_logo_region_contains_brand_blue(size: tuple[int, int]) -> None:
    a = _render(*size)
    logo = _region(a, 0.33, 0.06, 0.67, 0.16)
    dist = np.linalg.norm(logo - BRAND_BLUE, axis=-1)
    w, h = size
    assert (dist < 20).sum() > 0.002 * w * h
    # official colour reproduced (only sensor grain on top), not recoloured
    assert np.abs(np.median(logo[dist < 20], axis=0) - BRAND_BLUE).max() <= 2.5
    # the official gray "Auto" part is there as well
    brand_gray = np.array([64, 63, 63], np.float32)
    gray = np.linalg.norm(logo - brand_gray, axis=-1)
    assert (gray < 20).sum() > 0.001 * w * h
    assert np.abs(np.median(logo[gray < 20], axis=0) - brand_gray).max() <= 2.5


@pytest.mark.parametrize("size", [(2400, 1800), (3200, 2400)])
def test_brand_texts_stay_above_vehicle_zone(size: tuple[int, int]) -> None:
    a = _render(*size)
    luma = _luma(_region(a, 0.25, 0.27, 0.75, 0.45))
    assert luma.min() > 170  # no dark lettering below 27 % of the height
    # city name + contact line exist between the logo and 27 %
    text_band = _luma(_region(a, 0.30, 0.15, 0.70, 0.26))
    assert (text_band < 120).sum() > 0.0004 * size[0] * size[1]


@pytest.mark.parametrize("floor_horizon", [0.55, 0.62, 0.70])
def test_wall_floor_junction_at_floor_horizon(floor_horizon: float) -> None:
    w, h = 1200, 900
    image = render_placeholder_showroom(
        w, h, logo_path=LOGO, brand=ShowroomBrandText(), floor_horizon=floor_horizon
    )
    a = np.asarray(image).astype(np.float32)
    # bright wall above, warm wood below: the strongest drop of the blue
    # channel in the centre columns marks the junction
    col = a[:, int(0.3 * w) : int(0.7 * w), 2].mean(axis=1)
    lo, hi = int(0.40 * h), int(0.85 * h)
    drop = col[lo : hi - 4] - col[lo + 4 : hi]
    y = lo + int(np.argmax(drop)) + 2
    assert abs(y - floor_horizon * h) <= 0.012 * h


def test_missing_fonts_do_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(placeholder, "_font_index", dict)
    image = render_placeholder_showroom(640, 480, logo_path=LOGO, brand=ShowroomBrandText())
    assert image.size == (640, 480)


def test_missing_logo_still_renders(tmp_path: Path) -> None:
    image = render_placeholder_showroom(
        640, 480, logo_path=tmp_path / "missing.png", brand=ShowroomBrandText()
    )
    assert image.size == (640, 480)
