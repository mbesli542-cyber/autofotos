"""Deterministic brand wall: official logo + texts (layout engine and plate provider)."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.presets import BackgroundProvider, load_preset, parse_branding
from app.showroom.branding import BrandingConfig, apply_branding

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = REPO_ROOT / "public" / "brand"
BLUE = np.array([7, 136, 234], np.float32)  # official #0788EA
GRAY = np.array([64, 63, 63], np.float32)  # official #403F3F


def _wall(width=2400, height=1800, value=232):
    rgb = np.full((height, width, 3), value, np.uint8)
    rgb[int(0.62 * height) :] = (150, 108, 72)
    return rgb


def _flat(cfg: BrandingConfig) -> BrandingConfig:
    """No light matching and no stand-off shadow – pure colour checks."""
    return replace(cfg, light_match=0.0, mount=replace(cfg.mount, shadow_opacity=0.0))


def test_official_logo_colours_are_reproduced_exactly():
    cfg = _flat(BrandingConfig())
    on_light, layout = apply_branding(_wall(value=240), cfg, BRAND_DIR)
    on_dark, _ = apply_branding(_wall(value=20), cfg, BRAND_DIR)
    x0, y0, x1, y1 = layout.box("logo")
    light = on_light[y0:y1, x0:x1].astype(np.float32)
    dark = on_dark[y0:y1, x0:x1].astype(np.float32)
    opaque = np.abs(light - dark).max(axis=-1) <= 1  # background does not shine through
    logo = light[opaque]
    blue = logo[np.linalg.norm(logo - BLUE, axis=1) < 12]
    gray = logo[np.linalg.norm(logo - GRAY, axis=1) < 12]
    assert len(blue) > 500 and len(gray) > 300
    assert np.abs(np.median(blue, axis=0) - BLUE).max() <= 1.5
    assert np.abs(np.median(gray, axis=0) - GRAY).max() <= 1.5


def test_logo_keeps_its_aspect_ratio_and_configured_position():
    out, layout = apply_branding(_wall(), BrandingConfig(), BRAND_DIR)
    x0, y0, x1, y1 = layout.box("logo")
    with Image.open(BRAND_DIR / "official/AutoExperten_Logo.png") as image:
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(alpha > 8)
    official_aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    assert (x1 - x0) / (y1 - y0) == pytest.approx(official_aspect, rel=0.02)
    assert (x0 + x1) / 2 == pytest.approx(0.5 * 2400, abs=2)
    assert y0 == round(0.07 * 1800)
    assert (x1 - x0) <= 0.30 * 2400 + 1


def test_all_texts_are_drawn_below_the_logo_and_stay_in_the_upper_wall():
    _, layout = apply_branding(_wall(), BrandingConfig(), BRAND_DIR)
    names = [b[0] for b in layout.boxes]
    assert names == ["logo", "city", "website", "phone"]
    logo, city, web, phone = (layout.box(n) for n in names)
    assert logo[3] < city[1] < web[1] < phone[1]
    assert (web[2] - web[0]) < (city[2] - city[0])  # contact lines are secondary
    assert layout.bottom < 0.27 * 1800


def test_branding_is_deterministic_and_can_be_disabled():
    a, _ = apply_branding(_wall(), BrandingConfig(), BRAND_DIR)
    b, _ = apply_branding(_wall(), BrandingConfig(), BRAND_DIR)
    assert np.array_equal(a, b)
    wall = _wall()
    off, layout = apply_branding(wall, replace(BrandingConfig(), enabled=False), BRAND_DIR)
    assert np.array_equal(off, wall) and layout.boxes == ()


def test_wall_outside_the_branding_is_untouched():
    wall = _wall()
    out, layout = apply_branding(wall, BrandingConfig(), BRAND_DIR)
    below = int(layout.bottom + 0.01 * 1800)
    assert np.abs(out[below:].astype(int) - wall[below:].astype(int)).max() <= 1


def test_a_missing_official_logo_is_an_error_not_a_silent_omission(tmp_path):
    import shutil

    from app.showroom.branding import BrandingAssetError

    brand = tmp_path / "brand"
    (brand / "official").mkdir(parents=True)
    with pytest.raises(BrandingAssetError):
        apply_branding(_wall(), BrandingConfig(), brand)
    # once the file is (re)placed it is picked up without a restart
    shutil.copy(BRAND_DIR / "official/AutoExperten_Logo.png", brand / "official/AutoExperten_Logo.png")
    _, layout = apply_branding(_wall(), BrandingConfig(), brand)
    assert layout.box("logo") is not None


def test_upward_shadow_offset_at_the_top_edge_does_not_crash():
    from app.showroom.branding import MountConfig

    cfg = replace(BrandingConfig(), mount=MountConfig(shadow_offset=-0.004), logo=replace(BrandingConfig().logo, top=0.001))
    out, layout = apply_branding(_wall(), cfg, BRAND_DIR)
    assert layout.box("logo")[1] == 2


def test_preset_json_branding_is_parsed_and_tunable():
    cfg = parse_branding({"logo": {"top": 0.05, "maxWidth": 0.25}, "website": {"top": 0.2}, "lightMatch": 0})
    assert cfg.logo.top == 0.05 and cfg.logo.max_width == 0.25
    assert cfg.logo.file == "official/AutoExperten_Logo.png"  # defaults kept
    assert cfg.website.top == 0.2 and cfg.website.text == "www.autoexperten-rn.de"
    assert cfg.light_match == 0


def test_plate_provider_brands_every_plate_in_wall_space(settings):
    preset = load_preset(settings, "autoexperten_standard")
    provider = BackgroundProvider(settings)
    for shot in ("front_left_45", "left_side", "front", "rear_right_45"):
        showroom = provider.get(preset, shot, 1600, 1200)
        assert showroom.source == "plates" and showroom.key == shot
        assert not showroom.rgb.flags.writeable and not showroom.shadow.flags.writeable
        assert showroom.rgb.shape == (1200, 1600, 3) and showroom.shadow.shape == (1200, 1600)
        names = [b[0] for b in showroom.branding.boxes]
        assert names == ["logo", "city", "website", "phone"]
        logo = showroom.branding.box("logo")
        # centred on the wall, above the typical car's roof (with the clearance)
        u0, v0, u1, _ = showroom.plate.proxy_bbox
        assert abs((logo[0] + logo[2]) / 2 - 800) < 0.12 * 1600
        assert showroom.branding.bottom + preset.branding.clearance * 1200 < v0 * 1200
        # the main logo is clearly visible, the contact lines are secondary
        web = showroom.branding.box("website")
        assert 0.22 * 1600 < (logo[2] - logo[0]) < 0.40 * 1600
        assert (web[2] - web[0]) < 0.6 * (logo[2] - logo[0])


def test_plate_provider_caches_and_follows_file_changes(settings):
    import os
    import time

    preset = load_preset(settings, "autoexperten_standard")
    provider = BackgroundProvider(settings)
    first = provider.get(preset, "front", 800, 600)
    assert provider.get(preset, "front", 800, 600) is first
    image = settings.presets_dir / "autoexperten-standard" / "front.jpg"
    Image.fromarray(np.full((480, 640, 3), 90, np.uint8)).save(image)
    later = time.time() + 5
    os.utime(image, (later, later))
    second = provider.get(preset, "front", 800, 600)
    assert second is not first
    assert np.asarray(second.rgb)[590, 10].max() < 110  # the new (dark) plate is used


def test_an_unreadable_plate_makes_the_showroom_unavailable(settings):
    from app.presets import ShowroomUnavailableError

    preset = load_preset(settings, "autoexperten_standard")
    (settings.presets_dir / "autoexperten-standard" / "rear.jpg").write_bytes(b"half-copied")
    provider = BackgroundProvider(settings)
    assert provider.source(preset) == "missing"
    assert "rear" in provider.master_error(preset)
    with pytest.raises(ShowroomUnavailableError):
        provider.get(preset, "front", 800, 600)


def test_missing_logo_fails_the_background_with_a_configuration_error(settings):
    from app.presets import PresetConfigError

    preset = load_preset(settings, "autoexperten_standard")
    (settings.brand_dir / preset.branding.logo.file).unlink()
    with pytest.raises(PresetConfigError):
        BackgroundProvider(settings).get(preset, "front_left_45", 800, 600)
