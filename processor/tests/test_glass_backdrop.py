"""Glass backdrop (app/pipeline/glass_backdrop.py): the showroom seen THROUGH the glass of interior
shots – config, gate, composite, pipeline wiring.

Synthetic cockpit: a dark interior (dashboard with light stitching, a black steering wheel) under a
bright "windscreen" (the overcast car park with a mid-grey car), a black rear-view mirror inside the
glass area and a small quarter window at the left – the correct mask covers the two glass areas
without the mirror.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import REPO_ROOT
from app.pipeline import glass_backdrop as gb
from app.pipeline.debug import DebugSink
from app.pipeline.pipeline import process_photo
from app.presets import (
    GLASS_BACKDROP_SHOTS,
    BackgroundProvider,
    GlassBackdropConfig,
    GlassViewConfig,
    PresetConfigError,
    glass_backdrop_applies,
    load_preset,
    parse_preset,
)
from tests.conftest import encode_jpeg

W, H = 1600, 1200
GLASS = (200, 0, 1400, 420)  # x0, y0, x1, y1 of the windscreen
QUARTER = (0, 40, 150, 300)  # a small side window at the left border
MIRROR = (700, 60, 900, 170)  # the black rear-view mirror inside the windscreen area


def make_cockpit(seed: int = 11):
    """(rgb uint8, correct soft glass alpha float32)."""
    rng = np.random.default_rng(seed)
    rgb = np.zeros((H, W, 3), np.float32)
    rgb[:] = (34, 34, 36)  # dark dashboard / trim
    for y in range(460, H, 60):  # light stitching lines = interior structure with edges
        cv2.line(rgb, (0, y), (W, y), (120, 120, 118), 2)
    cv2.circle(rgb, (520, 820), 240, (12, 12, 12), 40)  # steering wheel rim
    cv2.rectangle(rgb, (980, 520), (1300, 760), (18, 18, 20), -1)  # display
    x0, y0, x1, y1 = GLASS
    rgb[y0:y1, x0:x1] = (196, 200, 206)  # overcast outside
    cv2.rectangle(rgb, (400, 200), (900, 400), (150, 152, 150), -1)  # a car outside
    cv2.rectangle(rgb, (950, 120), (1350, 400), (176, 174, 170), -1)  # a building
    qx0, qy0, qx1, qy1 = QUARTER
    rgb[qy0:qy1, qx0:qx1] = (190, 194, 200)
    mx0, my0, mx1, my1 = MIRROR
    rgb[my0:my1, mx0:mx1] = (10, 10, 12)
    rgb += rng.normal(0, 3, rgb.shape).astype(np.float32)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    mask = np.zeros((H, W), np.uint8)
    cv2.rectangle(mask, (x0, y0), (x1 - 1, y1 - 1), 255, -1)
    cv2.rectangle(mask, (qx0, qy0), (qx1 - 1, qy1 - 1), 255, -1)
    cv2.rectangle(mask, (mx0 - 4, my0 - 4), (mx1 + 3, my1 + 3), 0, -1)
    alpha = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 2.0)
    return rgb, np.clip(alpha, 0.0, 1.0).astype(np.float32)


@pytest.fixture(scope="module")
def cockpit():
    return make_cockpit()


def _preset_data() -> dict:
    return json.loads((REPO_ROOT / "public/presets/autoexperten-standard.json").read_text(encoding="utf-8"))


def _enabled_data(**changes) -> dict:
    data = _preset_data()
    data["glassBackdrop"]["enabled"] = True
    for key, value in changes.items():
        data["glassBackdrop"][key] = value
    return data


def _cfg(**changes) -> GlassBackdropConfig:
    return replace(GlassBackdropConfig(), enabled=True, **changes)


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _synthetic_plate(width: int = 1200, height: int = 900) -> gb.PlateContext:
    """A bright wall above a warm floor with a dark 'logo' block, horizon at 0.25."""
    plate = np.zeros((height, width, 3), np.uint8)
    plate[: int(0.45 * height)] = (225, 222, 218)
    plate[int(0.45 * height) :] = (170, 130, 90)
    cv2.rectangle(plate, (int(0.3 * width), int(0.08 * height)), (int(0.7 * width), int(0.2 * height)), (40, 60, 140), -1)
    return gb.PlateContext(rgb=plate, horizon_v=0.25)


# --------------------------------------------------------------------------- config


def test_glass_backdrop_is_off_in_the_shipped_preset_with_every_field_written_out():
    """The dealer can switch it on in the JSON; the shipped values are the code defaults (one
    place to change them), nothing hides behind a default."""
    data = _preset_data()
    assert data["glassBackdrop"]["enabled"] is False
    preset = parse_preset(data)
    assert preset.glass_backdrop == GlassBackdropConfig()
    assert not glass_backdrop_applies(preset.glass_backdrop, "cockpit")
    for name in ("autoexperten-haendler", "autoexperten-haendler-leicht", "autoexperten-haendler-kraeftig"):
        variant = json.loads((REPO_ROOT / "public/presets/variants" / f"{name}.json").read_text(encoding="utf-8"))
        assert variant["glassBackdrop"] == data["glassBackdrop"], name


def test_a_preset_without_the_section_keeps_the_earlier_behaviour():
    data = _preset_data()
    del data["glassBackdrop"]
    assert parse_preset(data).glass_backdrop == GlassBackdropConfig()


def test_glass_backdrop_config_is_validated():
    cfg = parse_preset(_enabled_data(shots=["cockpit"], glassKeep=0.1, side={"cropHeight": 0.4, "centerX": 0.1, "horizon": 0.5})).glass_backdrop
    assert cfg.enabled and cfg.shots == ("cockpit",) and cfg.glass_keep == 0.1
    assert cfg.side == GlassViewConfig(0.4, 0.1, 0.5) and cfg.view("side") is cfg.side and cfg.view("forward") is cfg.forward
    assert glass_backdrop_applies(cfg, "cockpit") and not glass_backdrop_applies(cfg, "front_interior")
    assert not glass_backdrop_applies(cfg, "wheel_detail") and not glass_backdrop_applies(cfg, None)
    assert GLASS_BACKDROP_SHOTS == {"cockpit", "front_interior", "driver_seat", "rear_seats"}
    for bad, match in (
        ({"shots": ["wheel_detail"]}, "shots"),  # detail/exterior shots never
        ({"shots": ["cockpit", "cockpit"]}, "shots"),
        ({"shots": "cockpit"}, "shots"),
        ({"blur": 0.1}, "blur"),
        ({"floorBlur": 1.5}, "floorBlur"),
        ({"glassKeep": 0.9}, "glassKeep"),
        ({"lightMatch": 2}, "lightMatch"),
        ({"minGlassFraction": 0.0}, "minGlassFraction"),
        ({"maxUndecided": 0.2}, "maxUndecided"),
        ({"maxBorderSides": 5}, "maxBorderSides"),
        ({"maxBorderSides": 2.5}, "maxBorderSides"),
        ({"maxInteriorStructure": 0.8}, "maxInteriorStructure"),
        ({"forward": {"cropHeight": 0.1, "centerX": 0.5, "horizon": 0.3}}, "forward.cropHeight"),
        ({"forward": {"cropHeight": 0.5, "centerX": 0.5, "horizon": 0.99}}, "forward.horizon"),
        ({"forward": {"cropHeight": 0.5, "centerX": 0.5}}, "forward needs every field"),
        ({"forward": {"cropHeight": 0.5, "centerX": 0.5, "horizon": 0.3, "zoom": 1}}, "Unknown preset field"),
        ({"side": 0.5}, "side must be an object"),
        ({"enabled": "yes"}, "enabled"),
        ({"plate": "front"}, "Unknown preset field"),
    ):
        with pytest.raises(PresetConfigError, match=match):
            parse_preset(_enabled_data(**bad))


def test_a_set_section_must_write_every_field_out():
    data = _enabled_data()
    del data["glassBackdrop"]["glassKeep"]
    del data["glassBackdrop"]["side"]
    with pytest.raises(PresetConfigError, match="glassBackdrop needs every field, missing: glassKeep, side"):
        parse_preset(data)
    data = _preset_data()
    data["glassBackdrop"] = []
    with pytest.raises(PresetConfigError, match="glassBackdrop must be an object"):
        parse_preset(data)


# --------------------------------------------------------------------------- gate


def test_the_correct_glass_mask_passes_the_gate(cockpit):
    rgb, alpha = cockpit
    gate = gb.assess_glass(rgb, alpha, _cfg())
    assert gate.passed, gate.as_dict()
    m = gate.metrics
    assert 0.2 < m["glassFraction"] < 0.4
    assert m["interiorStructure"] < 0.01  # the outside through the glass is bright
    assert m["interiorDarkOutside"] > 0.5  # the interior is not
    assert m["borderTouch"]["bottom"] == 0.0 and m["borderSides"] == 2  # top + left (quarter window)
    assert m["undecided"] < 0.005  # a 2 px feather
    assert gate.warning() is None


def test_no_glass_and_a_tiny_region_are_skipped(cockpit):
    rgb, alpha = cockpit
    gate = gb.assess_glass(rgb, np.zeros_like(alpha), _cfg())
    assert (gate.passed, gate.reason) == (False, "no_glass")
    assert gate.warning().code == gb.SKIP_CODE and "keine Scheibe" in gate.warning().message
    tiny = np.zeros_like(alpha)
    tiny[100:140, 300:400] = 1.0
    gate = gb.assess_glass(rgb, tiny, _cfg())
    assert (gate.passed, gate.reason) == (False, "glass_too_small")
    assert gb.assess_glass(rgb, tiny, _cfg(min_glass_fraction=0.001)).reason != "glass_too_small"


def test_an_undecided_boundary_is_skipped(cockpit):
    rgb, alpha = cockpit
    wide = cv2.GaussianBlur((alpha >= 0.5).astype(np.float32), (0, 0), 30.0)
    gate = gb.assess_glass(rgb, wide, _cfg())
    assert (gate.passed, gate.reason) == (False, "edge_undecided"), gate.as_dict()
    assert gate.metrics["undecided"] > 0.02
    assert "Scheibenrand" in gate.warning().message


def test_a_mask_that_leaked_into_the_interior_is_skipped(cockpit):
    """Glass never reaches the bottom of an interior photo; a region on all four borders is the
    whole frame."""
    rgb, alpha = cockpit
    cfg = _cfg()
    assert gb.assess_glass(rgb, np.ones_like(alpha), cfg).reason == "mask_leaked"
    to_bottom = alpha.copy()
    to_bottom[:, 600:900] = 1.0  # a column of 'glass' down to the bottom edge
    gate = gb.assess_glass(rgb, to_bottom, cfg)
    assert gate.reason == "mask_leaked" and gate.metrics["borderTouch"]["bottom"] > cfg.max_bottom_touch
    # the shipped limit allows top + left + right (a cockpit photo: windscreen and both side windows)
    three = alpha.copy()
    three[0:300, W - 60 :] = 1.0
    rgb3 = rgb.copy()
    rgb3[0:300, W - 60 :] = (196, 200, 206)
    gate = gb.assess_glass(rgb3, three, cfg)
    assert gate.passed and gate.metrics["borderSides"] == 3, gate.as_dict()
    assert gb.assess_glass(rgb3, three, _cfg(max_border_sides=2)).reason == "mask_leaked"


def test_interior_structure_inside_the_mask_is_skipped(cockpit):
    """The mirror or the steering wheel inside the mask: dark structure the outside never has."""
    rgb, alpha = cockpit
    cfg = _cfg()
    with_mirror = alpha.copy()
    mx0, my0, mx1, my1 = MIRROR
    with_mirror[my0 - 4 : my1 + 4, mx0 - 4 : mx1 + 4] = 1.0
    gate = gb.assess_glass(rgb, with_mirror, cfg)
    assert (gate.passed, gate.reason) == (False, "interior_structure"), gate.as_dict()
    assert gate.metrics["interiorStructure"] > cfg.max_interior_structure
    assert "Innenraum im Scheibenbereich" in gate.warning().message
    shifted = np.zeros_like(alpha)
    shifted[500:] = alpha[:-500]  # the dashboard and the steering wheel
    gate = gb.assess_glass(rgb, shifted, cfg)
    assert gate.reason == "interior_structure" and gate.metrics["interiorStructure"] > 0.5
    # a few px of trim along the outline never count: the core is eroded
    grown = cv2.dilate(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    assert gb.assess_glass(rgb, grown, cfg).passed


def test_an_invalid_mask_is_skipped_not_raised(cockpit):
    rgb, alpha = cockpit
    assert gb.assess_glass(rgb, alpha[:-1], _cfg()).reason == "mask_invalid"
    bad = alpha.copy()
    bad[0, 0] = np.nan
    assert gb.assess_glass(rgb, bad, _cfg()).reason == "mask_invalid"
    assert gb.assess_glass(rgb, alpha * 2.0, _cfg()).reason == "mask_invalid"


# --------------------------------------------------------------------------- composite


def test_plan_glass_crop_puts_the_horizon_where_the_view_says():
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.75, 0.5, 0.36))
    assert (crop.width, crop.height) == (2400, 1350)  # never wider than the plate
    assert crop.x0 == 0.0 and crop.y0 == 0.0  # clamped: the horizon cannot go lower than the plate allows
    assert crop.horizon == pytest.approx(0.254 * 1800 / 1350)
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.5, 0.15, 0.45))
    assert (crop.width, crop.height) == (1600, 900) and crop.x0 == 0.0  # the slat/plant side
    assert crop.y0 == pytest.approx(0.254 * 1800 - 0.45 * 900) and crop.horizon == pytest.approx(0.45)
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 4 / 3, GlassViewConfig(0.5, 0.5, 0.3))
    assert (crop.width, crop.height) == (1200, 900) and crop.x0 == 600.0


def test_pixels_outside_the_glass_are_bit_identical(cockpit):
    """The dealer's rule: nothing inside the car changes – every pixel with alpha 0 is the
    original byte for byte; the glass changes."""
    rgb, alpha = cockpit
    out, info = gb.compose_glass_backdrop(rgb, alpha, _synthetic_plate(), _cfg(glass_keep=0.1))
    assert out.shape == rgb.shape and out.dtype == np.uint8
    outside = alpha <= 0.0
    assert outside.mean() > 0.5
    assert np.array_equal(out[outside], rgb[outside])
    glass = alpha >= 0.999
    assert np.abs(out[glass].astype(int) - rgb[glass].astype(int)).mean() > 10
    # the mirror inside the windscreen stays the original (inside its feathered rim)
    mx0, my0, mx1, my1 = MIRROR
    assert np.array_equal(out[my0 + 10 : my1 - 10, mx0 + 10 : mx1 - 10], rgb[my0 + 10 : my1 - 10, mx0 + 10 : mx1 - 10])
    assert info["view"] == "forward" and info["blurSigmaPx"] == pytest.approx(0.005 * W)
    assert set(info) >= {"crop", "lightMatch", "grainSigma", "glassKeep", "backdrop"}
    assert info["backdrop"].shape == rgb.shape


def test_glass_keep_keeps_a_share_of_the_original_glass_pixels(cockpit):
    rgb, alpha = cockpit
    plate = _synthetic_plate()
    none, _ = gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(glass_keep=0.0, grain=0.0))
    some, _ = gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(glass_keep=0.5, grain=0.0))
    glass = alpha >= 0.999
    # with half the original kept, the composite lies between the pure backdrop and the original
    d_none = np.abs(none[glass].astype(float) - rgb[glass]).mean()
    d_some = np.abs(some[glass].astype(float) - rgb[glass]).mean()
    assert 0.3 * d_none < d_some < 0.7 * d_none


def test_the_side_view_uses_the_side_crop_and_the_light_match_is_limited(cockpit):
    rgb, alpha = cockpit
    plate = _synthetic_plate()
    _, forward = gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(), view="forward")
    _, side = gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(), view="side")
    assert forward["crop"] != side["crop"]
    for gain in (*forward["lightMatch"]["gain"], *side["lightMatch"]["gain"]):
        assert 0.5 * (1 - 0.12) <= gain <= 2.0 * (1 + 0.12)  # ±1 EV, ±12 % per channel
    with pytest.raises(ValueError):
        gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(), view="roof")


# --------------------------------------------------------------------------- mask source


def test_file_provider_loads_a_png_next_to_the_photo(tmp_path, cockpit):
    rgb, alpha = cockpit
    photo = tmp_path / "09_cockpit.jpg"
    mask_path = gb.glass_mask_beside(photo)
    assert mask_path.name == "09_cockpit-glass.png"
    assert gb.file_glass_masks(mask_path)(rgb, "cockpit") is None  # no file = no glass
    small = cv2.resize((alpha * 255).astype(np.uint8), (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    Image.fromarray(small).save(mask_path)
    mask = gb.file_glass_masks(mask_path)(rgb, "cockpit")
    assert mask is not None and mask.alpha.shape == (H, W) and mask.alpha.dtype == np.float32
    assert mask.source == "file:09_cockpit-glass.png" and mask.panes == ()
    assert np.abs(mask.alpha - alpha).mean() < 0.02
    (pane,) = mask.panes_for("side")
    assert pane.view == "side" and pane.alpha is mask.alpha


# --------------------------------------------------------------------------- pipeline


@pytest.fixture
def run(settings, fake_segmenter, cockpit):
    def _run(data: dict | None = None, *, provider=None, shot_key="cockpit", debug=None):
        preset = parse_preset(data) if data is not None else load_preset(settings, "autoexperten_standard")
        return process_photo(
            encode_jpeg(cockpit[0], 97),
            preset=preset,
            segmenter=fake_segmenter,
            backgrounds=BackgroundProvider(settings),
            shot_key=shot_key,
            debug=debug or DebugSink(None),
            glass_masks=provider,
        )

    return _run


def _provider(alpha, **kw):
    return lambda _rgb, _shot: gb.GlassMask(alpha=alpha, source="test", **kw)


def test_disabled_interior_shots_stay_the_plain_passthrough(run, cockpit):
    result = run(provider=_provider(cockpit[1]))
    assert result.metadata["shotKind"] == "interior_passthrough" and result.warnings == []
    assert "glassBackdrop" not in result.metadata


def test_enabled_interior_shot_gets_the_showroom_through_the_glass(run, cockpit, tmp_path):
    rgb, alpha = cockpit
    sink = DebugSink(tmp_path / "dbg")
    result = run(_enabled_data(), provider=_provider(alpha), debug=sink)
    assert result.metadata["shotKind"] == "interior_glass_backdrop" and result.warnings == []
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] and meta["gate"]["passed"] and meta["maskSource"] == "test" and meta["plate"] == "front"
    assert [p["view"] for p in meta["panes"]] == ["forward"]
    assert "backdrop" not in meta["panes"][0]  # the image is a debug file, not metadata
    assert set(meta["timingsMs"]) >= {"mask", "gate", "plate", "composite"}
    out = _decode(result.jpeg)
    assert out.shape == rgb.shape  # the original framing (16:9 stays 16:9)
    outside = alpha <= 0.0
    smooth = lambda x: cv2.GaussianBlur(x.astype(np.float32), (0, 0), 2)  # noqa: E731 – JPEG twice
    assert np.abs(smooth(out) - smooth(rgb))[outside].mean() < 1.5
    glass = alpha >= 0.999
    assert np.abs(out[glass].astype(int) - rgb[glass].astype(int)).mean() > 10
    for name in ("original.jpg", "mask.png", "geometry.jpg", "background.jpg", "final.jpg", "metadata.json"):
        assert (tmp_path / "dbg" / name).is_file(), name
    assert Image.open(tmp_path / "dbg" / "mask.png").size == (W, H)


def test_without_a_mask_provider_the_original_stays_with_a_hint(run):
    result = run(_enabled_data(), provider=None)
    assert result.metadata["shotKind"] == "interior_passthrough"
    assert [w.code for w in result.warnings] == [gb.SKIP_CODE]
    assert result.metadata["glassBackdrop"] == {"applied": False, "reason": "no_mask_provider", "timingsMs": {}}
    assert "nicht verfügbar" in result.warnings[0].message


def test_a_rejected_mask_leaves_the_original_with_the_gate_reason(run, cockpit):
    rgb, alpha = cockpit
    with_mirror = alpha.copy()
    mx0, my0, mx1, my1 = MIRROR
    with_mirror[my0 - 4 : my1 + 4, mx0 - 4 : mx1 + 4] = 1.0
    result = run(_enabled_data(), provider=_provider(with_mirror))
    assert result.metadata["shotKind"] == "interior_passthrough"
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] is False and meta["reason"] == "interior_structure" and meta["gate"]["passed"] is False
    assert [(w.code, w.message) for w in result.warnings] == [(gb.SKIP_CODE, gb.MESSAGES["interior_structure"])]
    smooth = lambda x: cv2.GaussianBlur(x.astype(np.float32), (0, 0), 2)  # noqa: E731
    assert np.abs(smooth(_decode(result.jpeg)) - smooth(rgb)).mean() < 1.5
    # the provider says "no glass" (None) → the same hint as an empty mask
    result = run(_enabled_data(), provider=lambda _rgb, _shot: None)
    assert result.metadata["glassBackdrop"]["reason"] == "no_glass"
    assert result.warnings[0].message == gb.MESSAGES["no_glass"]


def test_shots_not_listed_and_detail_shots_are_untouched(run, cockpit):
    result = run(_enabled_data(shots=["rear_seats"]), provider=_provider(cockpit[1]))
    assert result.metadata["shotKind"] == "interior_passthrough" and result.warnings == []
    result = run(_enabled_data(), provider=_provider(cockpit[1]), shot_key="door_controls")
    assert result.metadata["shotKind"] == "interior_passthrough" and "glassBackdrop" not in result.metadata


def test_panes_with_their_own_views(run, cockpit):
    rgb, alpha = cockpit
    windscreen = alpha.copy()
    windscreen[:, : QUARTER[2] + 10] = 0.0
    quarter = alpha.copy()
    quarter[:, QUARTER[2] + 10 :] = 0.0
    provider = _provider(alpha, panes=(gb.GlassPane(windscreen, "forward"), gb.GlassPane(quarter, "side")))
    result = run(_enabled_data(), provider=provider)
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] and [p["view"] for p in meta["panes"]] == ["forward", "side"]
    assert meta["panes"][0]["crop"] != meta["panes"][1]["crop"]
    # a pane of the wrong size never reaches the composite
    provider = _provider(alpha, panes=(gb.GlassPane(windscreen[:-1], "forward"),))
    result = run(_enabled_data(), provider=provider)
    assert result.metadata["glassBackdrop"]["reason"] == "mask_invalid"


def test_a_missing_plate_set_leaves_the_original_with_a_hint(run, cockpit, plates_dir):
    (plates_dir / "plates.json").unlink()
    result = run(_enabled_data(), provider=_provider(cockpit[1]))
    assert result.metadata["shotKind"] == "interior_passthrough"
    assert result.metadata["glassBackdrop"]["reason"] == "showroom_unavailable"
    assert "Showroom-Hintergrund nicht verfügbar" in result.warnings[0].message
