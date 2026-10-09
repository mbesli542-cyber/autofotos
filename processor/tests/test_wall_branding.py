"""Official branding composited in WALL SPACE through each plate's wall homography."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.showroom.branding import BrandingConfig, TextConfig, _srgb_to_linear
from app.showroom.plates import load_plate_set
from app.showroom.wall_branding import apply_wall_branding, canvas_size, output_density

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = REPO_ROOT / "public" / "brand"
BLUE = np.array([7, 136, 234], np.float32)  # official #0788EA
W, H = 1600, 1200


@pytest.fixture(scope="module")
def plates(synthetic_plates):
    return load_plate_set(synthetic_plates / "plates.json")


def _logo_only(**logo) -> BrandingConfig:
    cfg = BrandingConfig(light_match=0.0)
    empty = TextConfig(text="")
    return replace(
        cfg,
        logo=replace(cfg.logo, **logo),
        city=empty,
        website=empty,
        phone=empty,
        mount=replace(cfg.mount, shadow_opacity=0.0),
    )


def _logo_alpha() -> np.ndarray:
    """Official logo alpha trimmed like the branding layer does."""
    with Image.open(BRAND_DIR / "official/AutoExperten_Logo.png") as image:
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(alpha > 8)
    return alpha[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _logo_aspect() -> float:
    alpha = _logo_alpha()
    return alpha.shape[1] / alpha.shape[0]


def _albedo_srgb(plate) -> np.ndarray:
    lin = np.asarray(plate.wall_albedo, np.float64)
    s = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.round(s * 255).astype(np.uint8)


@pytest.mark.parametrize("shot", ["front_left_45", "left_side", "rear_right_45"])
def test_logo_lands_where_the_wall_homography_says(plates, shot):
    plate = plates.plate(shot)
    cfg = _logo_only(center_x=0.5, top=0.1, max_width=0.4, max_height=0.3)
    wall = np.full((H, W, 3), 210, np.uint8)
    out, layout = apply_wall_branding(wall, plate, cfg, BRAND_DIR)
    # expected logo rectangle on the wall (metres), projected with the homography
    area = plate.brand_area
    width_m = min(0.4 * area.width, 0.3 * area.height * _logo_aspect())
    height_m = width_m / _logo_aspect()
    x0 = area.x_min + 0.5 * area.width - width_m / 2
    z0 = area.z_max - 0.1 * area.height
    corners = [plate.wall_to_uv(x, z) for x in (x0, x0 + width_m) for z in (z0, z0 - height_m)]
    us = [float(u) * W for u, _ in corners]
    vs = [float(v) * H for _, v in corners]
    changed = np.nonzero(np.abs(out.astype(int) - wall.astype(int)).max(axis=-1) > 0)  # uniform wall: any change
    # the glyphs reach the left, right and top edge of the (perspective) logo rectangle
    assert min(changed[1]) == pytest.approx(min(us), abs=3)
    assert max(changed[1]) == pytest.approx(max(us), abs=3)
    assert min(changed[0]) == pytest.approx(min(vs), abs=3)
    assert max(changed[0]) <= max(vs) + 2
    # the logo's opaque pixels are centred where the homography maps the PNG's centroid
    alpha = _logo_alpha()
    ys, xs = np.nonzero(alpha[::3, ::3] > 127)
    wx = x0 + (3 * xs + 1.5) / alpha.shape[1] * width_m
    wz = z0 - (3 * ys + 1.5) / alpha.shape[0] * height_m
    hom = plate.wall_h @ np.stack([wx, wz, np.ones_like(wx)])
    weight = 1.0 / hom[2] ** 3  # projected area of each sample (Jacobian of the homography)
    cu = float(np.sum(hom[0] / hom[2] * weight) / weight.sum())
    cv = float(np.sum(hom[1] / hom[2] * weight) / weight.sum())
    strong = np.nonzero(np.abs(out.astype(int) - wall.astype(int)).max(axis=-1) > 60)
    assert strong[1].mean() == pytest.approx(cu * W - 0.5, abs=2.5)
    assert strong[0].mean() == pytest.approx(cv * H - 0.5, abs=2.5)
    # the projected box reported for the headroom rule encloses the drawn logo
    box = layout.box("logo")
    assert box[0] <= min(changed[1]) and box[2] > max(changed[1])
    assert box[1] <= min(changed[0]) and box[3] > max(changed[0])


def test_wall_without_branding_stays_bit_exact(plates):
    plate = plates.plate("front_left_45")
    rng = np.random.default_rng(4)
    wall = rng.integers(60, 250, (H, W, 3), dtype=np.uint8)  # textured plate
    cfg = replace(BrandingConfig(), mount=replace(BrandingConfig().mount, shadow_opacity=0.0))
    out, layout = apply_wall_branding(wall, plate, cfg, BRAND_DIR)
    inside = np.zeros((H, W), bool)
    for _, x0, y0, x1, y1 in layout.boxes:
        inside[max(y0 - 2, 0) : y1 + 2, max(x0 - 2, 0) : x1 + 2] = True
    assert np.array_equal(out[~inside], wall[~inside])
    assert not np.array_equal(out[inside], wall[inside])


def test_relighting_is_multiplicative_in_linear_light(plates):
    """A matte print: the branded pixel = plate light × print reflectance."""
    plate = plates.plate("front")
    cfg = _logo_only()
    bright, _ = apply_wall_branding(np.full((H, W, 3), 220, np.uint8), plate, cfg, BRAND_DIR)
    dim_value = 150
    dim, _ = apply_wall_branding(np.full((H, W, 3), dim_value, np.uint8), plate, cfg, BRAND_DIR)
    logo = np.abs(bright.astype(int) - 220).max(axis=-1) > 40
    ratio_expected = _srgb_to_linear(np.float32(dim_value / 255)) / _srgb_to_linear(np.float32(220 / 255))
    lin_b = _srgb_to_linear(bright[logo].astype(np.float32) / 255)
    lin_d = _srgb_to_linear(dim[logo].astype(np.float32) / 255)
    sel = lin_b > 0.05  # avoid quantisation noise of very dark pixels
    assert np.median(lin_d[sel] / lin_b[sel]) == pytest.approx(float(ratio_expected), rel=0.03)


def test_official_logo_colours_on_a_wall_lit_at_its_albedo(plates):
    plate = plates.plate("front")
    wall = np.broadcast_to(_albedo_srgb(plate), (H, W, 3)).copy()
    out, layout = apply_wall_branding(wall, plate, _logo_only(max_width=0.9, max_height=0.5), BRAND_DIR)
    x0, y0, x1, y1 = layout.box("logo")
    region = out[y0:y1, x0:x1].astype(np.float32)
    near_blue = (np.linalg.norm(region - BLUE, axis=-1) < 25).astype(np.uint8)
    interior = cv2.erode(near_blue, np.ones((5, 5), np.uint8)).astype(bool)  # no anti-aliased edge pixels
    blue = region[interior]
    assert len(blue) > 200
    # the official PNG has soft alpha (≈ 0.8–1.0) inside the letters: a few levels of wall show through
    assert np.abs(np.median(blue, axis=0) - BLUE).max() <= 6


def test_canvas_is_dense_enough_that_the_logo_is_never_upscaled(plates):
    for shot in ("front_left_45", "left_side", "front"):
        plate = plates.plate(shot)
        for width, height in ((1600, 1200), (3200, 2400)):
            _, _, density = canvas_size(plate, width, height)
            assert density >= 2.0 * output_density(plate, width, height) - 1e-6


def test_disabled_branding_returns_the_plate_unchanged(plates):
    wall = np.full((H, W, 3), 200, np.uint8)
    out, layout = apply_wall_branding(wall, plates.plate("rear"), replace(BrandingConfig(), enabled=False), BRAND_DIR)
    assert out is wall and layout.boxes == ()
