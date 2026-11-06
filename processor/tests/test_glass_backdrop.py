"""Glass backdrop (app/pipeline/glass_backdrop.py): the showroom seen THROUGH the glass of interior
shots – config, gate, panes/views, composite, pipeline wiring.

Synthetic cockpit: a dark interior (dashboard with light stitching, a black steering wheel) under a
bright "windscreen" (the overcast car park with a mid-grey car), a black rear-view mirror inside the
glass area, a small quarter window at the left border and a side window at the right border – the
correct mask covers the three glass areas without the mirror (three panes).
"""

from __future__ import annotations

import io
import json
from dataclasses import replace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import REPO_ROOT, Settings
from app.pipeline import glass_backdrop as gb
from app.pipeline.debug import DebugSink
from app.pipeline.detail_backdrop import Crop
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
GLASS = (200, 0, 1340, 420)  # x0, y0, x1, y1 of the windscreen (centroid left of the middle)
QUARTER = (0, 40, 150, 300)  # a small side window at the left border
RIGHT = (1500, 40, 1600, 300)  # a side window at the right border (the car's right side)
MIRROR = (650, 60, 950, 200)  # the black rear-view mirror inside the windscreen area
#: Branding boxes of the shipped plate at 2400 × 1800 (logo, "Schwetzingen" line).
SHIPPED_BOXES = ((578, 104, 1737, 295), (594, 305, 1721, 357))


def make_cockpit(seed: int = 11):
    """(rgb uint8, correct soft glass alpha float32)."""
    rng = np.random.default_rng(seed)
    rgb = np.zeros((H, W, 3), np.float32)
    rgb[:] = (34, 34, 36)  # dark dashboard / trim
    for y in range(460, H, 60):  # light stitching lines = interior structure with edges
        cv2.line(rgb, (0, y), (W, y), (120, 120, 118), 2)
    cv2.circle(rgb, (520, 820), 240, (12, 12, 12), 40)  # steering wheel rim
    cv2.rectangle(rgb, (980, 520), (1300, 760), (18, 18, 20), -1)  # display
    mask = np.zeros((H, W), np.uint8)
    for x0, y0, x1, y1 in (GLASS, QUARTER, RIGHT):
        rgb[y0:y1, x0:x1] = (196, 200, 206)  # overcast outside
        cv2.rectangle(mask, (x0, y0), (x1 - 1, y1 - 1), 255, -1)
    cv2.rectangle(rgb, (400, 200), (900, 400), (150, 152, 150), -1)  # a car outside
    cv2.rectangle(rgb, (950, 120), (1350, 400), (176, 174, 170), -1)  # a building
    mx0, my0, mx1, my1 = MIRROR
    rgb[my0:my1, mx0:mx1] = (10, 10, 12)
    cv2.rectangle(mask, (mx0 - 4, my0 - 4), (mx1 + 3, my1 + 3), 0, -1)
    rgb += rng.normal(0, 3, rgb.shape).astype(np.float32)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
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


def _view(**changes) -> dict:
    return {"cropHeight": 0.3, "centerX": 0.0, "top": 0.22, "blurScale": 1.0, "mirrorRight": True, **changes}


def _cfg(**changes) -> GlassBackdropConfig:
    return replace(GlassBackdropConfig(), enabled=True, **changes)


def _below_branding(settings) -> dict:
    """side/rear views that start below the SYNTHETIC plate set's branding (tests/plate_fixtures.py
    puts it elsewhere than the shipped plate): the pipeline tests exercise the views, not the
    layout; the shipped layout is checked against the recorded boxes."""
    preset = load_preset(settings, "autoexperten_standard")
    showroom = BackgroundProvider(settings).get(preset, gb.GLASS_PLATE, 1200, 900)
    bottom = max(b[4] for b in showroom.branding.boxes)
    top = round(min(0.6, (bottom + 60) / 900.0), 3)
    return {"side": _view(top=top), "rear": _view(top=top, centerX=0.5, blurScale=1.5, mirrorRight=False)}


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _synthetic_plate(width: int = 1200, height: int = 900) -> gb.PlateContext:
    """A bright wall above a warm floor, a dark 'logo' block in the upper middle, a green 'plant'
    at the left; horizon at 0.25. The branding box is the logo block."""
    plate = np.zeros((height, width, 3), np.uint8)
    plate[: int(0.45 * height)] = (225, 222, 218)
    plate[int(0.45 * height) :] = (170, 130, 90)
    logo = (int(0.3 * width), int(0.08 * height), int(0.7 * width), int(0.2 * height))
    cv2.rectangle(plate, logo[:2], logo[2:], (40, 60, 140), -1)
    cv2.rectangle(plate, (0, int(0.25 * height)), (int(0.12 * width), int(0.7 * height)), (70, 110, 60), -1)
    return gb.PlateContext(rgb=plate, horizon_v=0.25, branding_boxes=(logo,))


# --------------------------------------------------------------------------- config


def test_glass_backdrop_is_off_in_the_shipped_preset_with_every_field_written_out():
    """The dealer can switch it on in the JSON; the shipped values are the code defaults (one
    place to change them), nothing hides behind a default."""
    data = _preset_data()
    assert data["glassBackdrop"]["enabled"] is False
    assert set(data["glassBackdrop"]) >= {"forward", "side", "rear", "blur", "glassKeep"}
    preset = parse_preset(data)
    assert preset.glass_backdrop == GlassBackdropConfig()
    assert not glass_backdrop_applies(preset.glass_backdrop, "cockpit")
    for name in ("autoexperten-haendler", "autoexperten-haendler-leicht", "autoexperten-haendler-kraeftig"):
        variant = json.loads((REPO_ROOT / "public/presets/variants" / f"{name}.json").read_text(encoding="utf-8"))
        assert variant["glassBackdrop"] == data["glassBackdrop"], name


def test_the_shipped_views_keep_the_lettering_out_of_side_and_rear_crops():
    """Only the forward view may show the branding: the side/rear crops start below the shipped
    plate's lettering (top 0.22 > 357/1800) and never reach it – at any photo aspect, including the
    blur margin."""
    cfg = GlassBackdropConfig()
    assert cfg.forward.mirror_right is False and cfg.side.mirror_right is True and cfg.rear.blur_scale > cfg.side.blur_scale
    assert cfg.side.crop_height <= 0.3 and cfg.rear.crop_height <= 0.3 and cfg.side.center_x == 0.0
    for aspect in (16 / 9, 4 / 3, 3 / 4, 1.0):
        for view in ("side", "rear"):
            crop = gb.plan_glass_crop(2400, 1800, 0.254, aspect, cfg.view(view))
            margin = gb.BRANDING_MARGIN_SIGMAS * gb.blur_sigma_px(cfg, view, 2400.0) * crop.height / 1350.0
            assert not gb.branding_in_crop(crop, SHIPPED_BOXES, margin), (view, aspect, crop)
    forward = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, cfg.forward)
    assert gb.branding_in_crop(forward, SHIPPED_BOXES, 0.0)  # the brand wall ahead, on purpose


def test_a_preset_without_the_section_keeps_the_earlier_behaviour():
    data = _preset_data()
    del data["glassBackdrop"]
    assert parse_preset(data).glass_backdrop == GlassBackdropConfig()


def test_glass_backdrop_config_is_validated():
    cfg = parse_preset(_enabled_data(shots=["cockpit"], glassKeep=0.1, side=_view(cropHeight=0.2, blurScale=2))).glass_backdrop
    assert cfg.enabled and cfg.shots == ("cockpit",) and cfg.glass_keep == 0.1
    assert cfg.side == GlassViewConfig(0.2, 0.0, 0.22, 2.0, True)
    assert cfg.view("side") is cfg.side and cfg.view("rear") is cfg.rear and cfg.view("forward") is cfg.forward
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
        ({"forward": _view(cropHeight=0.05)}, "forward.cropHeight"),
        ({"side": _view(top=0.95)}, "side.top"),
        ({"rear": _view(blurScale=5)}, "rear.blurScale"),
        ({"side": _view(mirrorRight="yes")}, "mirrorRight"),
        ({"forward": {"cropHeight": 0.5, "centerX": 0.5, "top": 0.3}}, "forward needs every field"),
        ({"forward": _view(horizon=0.3)}, "Unknown preset field"),
        ({"side": 0.5}, "side must be an object"),
        ({"enabled": "yes"}, "enabled"),
        ({"plate": "front"}, "Unknown preset field"),
    ):
        with pytest.raises(PresetConfigError, match=match):
            parse_preset(_enabled_data(**bad))


def test_a_set_section_must_write_every_field_out():
    data = _enabled_data()
    del data["glassBackdrop"]["glassKeep"]
    del data["glassBackdrop"]["rear"]
    with pytest.raises(PresetConfigError, match="glassBackdrop needs every field, missing: glassKeep, rear"):
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
    assert m["borderTouch"]["bottom"] == 0.0 and m["borderSides"] == 3  # top, left and right
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
    whole frame. The shipped limit allows top + left + right (windscreen and both side windows)."""
    rgb, alpha = cockpit
    cfg = _cfg()
    assert gb.assess_glass(rgb, np.ones_like(alpha), cfg).reason == "mask_leaked"
    to_bottom = alpha.copy()
    to_bottom[:, 600:900] = 1.0  # a column of 'glass' down to the bottom edge
    gate = gb.assess_glass(rgb, to_bottom, cfg)
    assert gate.reason == "mask_leaked" and gate.metrics["borderTouch"]["bottom"] > cfg.max_bottom_touch
    assert gb.assess_glass(rgb, alpha, _cfg(max_border_sides=2)).reason == "mask_leaked"


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


def test_a_side_or_rear_crop_with_branding_in_view_is_rejected():
    """Letter fragments can never appear by accident: side/rear crops are checked against the
    plate's branding boxes (grown by the blur margin); the forward view is exempt."""
    side = Crop(0.0, 396.0, 960.0, 540.0, 0.1)  # the shipped side crop at 16:9: just below the lettering
    assert not gb.branding_in_crop(side, SHIPPED_BOXES, 20.0)
    assert gb.branding_in_crop(side, SHIPPED_BOXES, 50.0)
    assert gb.assess_crop(side, "side", SHIPPED_BOXES, 20.0).passed
    gate = gb.assess_crop(side, "side", SHIPPED_BOXES, 50.0)
    assert (gate.passed, gate.reason) == (False, "branding_in_view") and gate.metrics["brandingMarginPx"] == 50.0
    assert "Schriftzug" in gate.warning().message
    high = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.3, 0.0, 0.1, 1.0, True))
    assert gb.assess_crop(high, "side", SHIPPED_BOXES, 0.0).reason == "branding_in_view"
    assert gb.assess_crop(high, "rear", SHIPPED_BOXES, 0.0).reason == "branding_in_view"
    assert gb.assess_crop(high, "forward", SHIPPED_BOXES, 0.0).passed


# --------------------------------------------------------------------------- panes and views


def test_panes_are_the_connected_components_with_a_view_by_shot_and_position(cockpit):
    rgb, alpha = cockpit
    panes = gb.split_panes(alpha)
    assert len(panes) == 3
    centroids = sorted(gb.pane_centroid(p) for p in panes)
    assert centroids[0][0] < 0.1 and 0.45 < centroids[1][0] < 0.55 and centroids[2][0] > 0.9
    assert all(v < 0.3 for _, v in centroids)
    assert np.array_equal(np.max(panes, axis=0), alpha)  # nothing lost, nothing added
    # two panes whose feather tails meet in the pillar between them stay two panes
    pair = np.zeros((300, 400), np.uint8)
    cv2.rectangle(pair, (20, 20), (190, 200), 255, -1)
    cv2.rectangle(pair, (200, 20), (380, 200), 255, -1)
    bridged = cv2.GaussianBlur(pair.astype(np.float32) / 255.0, (0, 0), 2.5)
    assert len(gb.split_panes(bridged)) == 2 and len(gb.split_panes((pair > 0).astype(np.float32))) == 2
    assert np.array_equal(np.max(gb.split_panes(bridged), axis=0), bridged.astype(np.float32))
    assert gb.view_for_pane("cockpit", 0.5, 0.2) == "forward" and gb.view_for_pane("front_interior", 0.9, 0.1) == "forward"
    assert gb.view_for_pane("driver_seat", 0.5, 0.2) == "side" and gb.view_for_pane("driver_seat", 0.9, 0.2) == "side"
    assert gb.view_for_pane("rear_seats", 0.5, 0.2) == "rear"  # the wide pane in the upper middle
    assert gb.view_for_pane("rear_seats", 0.2, 0.2) == "side" and gb.view_for_pane("rear_seats", 0.5, 0.7) == "side"
    assert gb.view_for_pane(None, 0.5, 0.5) == "forward"
    mask = gb.GlassMask(alpha, source="test")
    assert [p.view for p in mask.panes_for("rear_seats")] == ["rear", "side", "side"] or sorted(
        p.view for p in mask.panes_for("rear_seats")
    ) == ["rear", "side", "side"]
    given = gb.GlassMask(alpha, panes=(gb.GlassPane(panes[0], "forward"), gb.GlassPane(panes[1])))
    resolved = given.panes_for("driver_seat")
    assert [p.view for p in resolved] == ["forward", "side"]  # a given view is kept, None is resolved
    assert gb.pane_centroid(np.zeros((10, 10), np.float32)) == (0.5, 0.5)


# --------------------------------------------------------------------------- composite


def test_plan_glass_crop_anchors_the_crop_top_and_clamps_into_the_plate():
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.75, 0.5, 0.0, 1.0, False))
    assert (crop.width, crop.height, crop.x0, crop.y0) == (2400, 1350, 0.0, 0.0)  # never wider than the plate
    assert crop.horizon == pytest.approx(0.254 * 1800 / 1350)
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.3, 0.0, 0.22, 1.0, True))
    assert (crop.width, crop.height, crop.x0) == (960, 540, 0.0) and crop.y0 == pytest.approx(0.22 * 1800)
    assert crop.horizon == pytest.approx((0.254 * 1800 - 396) / 540)
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 16 / 9, GlassViewConfig(0.3, 0.5, 0.22, 1.5, False))
    assert crop.x0 == 720.0  # the plain panel in the middle
    crop = gb.plan_glass_crop(2400, 1800, 0.254, 4 / 3, GlassViewConfig(0.3, 0.9, 0.9, 1.0, False))
    assert (crop.width, crop.height) == (720, 540) and crop.x0 == 1680.0 and crop.y0 == 1260.0  # clamped


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
    assert info["view"] == "forward" and info["mirrored"] is False and info["blurSigmaPx"] == pytest.approx(0.007 * W)
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


def test_views_use_their_own_crop_blur_and_mirror(cockpit):
    rgb, alpha = cockpit
    plate = _synthetic_plate()
    cfg = _cfg(light_match=0.0, grain=0.0)
    _, forward = gb.compose_glass_backdrop(rgb, alpha, plate, cfg, view="forward")
    _, side = gb.compose_glass_backdrop(rgb, alpha, plate, cfg, view="side")
    _, rear = gb.compose_glass_backdrop(rgb, alpha, plate, cfg, view="rear")
    _, mirrored = gb.compose_glass_backdrop(rgb, alpha, plate, cfg, view="side", mirrored=True)
    assert forward["crop"] != side["crop"] != rear["crop"]
    assert rear["blurSigmaPx"] == pytest.approx(1.5 * side["blurSigmaPx"])
    assert mirrored["mirrored"] is True and mirrored["crop"] == side["crop"]
    flipped = side["backdrop"][:, ::-1].astype(int)
    assert np.abs(mirrored["backdrop"].astype(int) - flipped).max() <= 2  # the same crop, flipped
    assert np.abs(side["backdrop"].astype(int) - mirrored["backdrop"].astype(int)).mean() > 2  # not symmetric
    _, matched = gb.compose_glass_backdrop(rgb, alpha, plate, _cfg(), view="side")
    for gain in matched["lightMatch"]["gain"]:
        assert 0.5 * (1 - 0.12) <= gain <= 2.0 * (1 + 0.12)  # ±1 EV, ±12 % per channel
    with pytest.raises(ValueError):
        gb.compose_glass_backdrop(rgb, alpha, plate, cfg, view="roof")


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
    assert len(mask.panes_for("cockpit")) == 3 and {p.view for p in mask.panes_for("cockpit")} == {"forward"}


# --------------------------------------------------------------------------- pipeline


@pytest.fixture
def run(settings, fake_segmenter, cockpit):
    def _run(data: dict | None = None, *, provider=None, shot_key="cockpit", debug=None, keep_views=False):
        """``keep_views``: use the side/rear views of ``data`` as they are (default: moved below the
        synthetic plate's branding, see _below_branding)."""
        if data is not None and data["glassBackdrop"]["enabled"] and not keep_views:
            data = {**data, "glassBackdrop": {**data["glassBackdrop"], **_below_branding(settings)}}
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


def test_enabled_cockpit_gets_the_showroom_through_the_glass(run, cockpit, tmp_path):
    rgb, alpha = cockpit
    sink = DebugSink(tmp_path / "dbg")
    result = run(_enabled_data(), provider=_provider(alpha), debug=sink)
    assert result.metadata["shotKind"] == "interior_glass_backdrop" and result.warnings == []
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] and meta["gate"]["passed"] and meta["maskSource"] == "test" and meta["plate"] == "front"
    (group,) = meta["panes"]  # three panes, all forward and unmirrored → one backdrop
    assert group["view"] == "forward" and group["mirrored"] is False and len(group["panes"]) == 3
    assert "backdrop" not in group  # the image is a debug file, not metadata
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


def test_side_panes_get_the_side_view_and_the_right_pane_is_mirrored(run, cockpit):
    """driver_seat: every pane looks sideways; the pane in the right half of the photo (the car's
    right side) gets the mirrored crop – two backdrops."""
    result = run(_enabled_data(), provider=_provider(cockpit[1]), shot_key="driver_seat")
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"], meta
    groups = {(g["view"], g["mirrored"]): g for g in meta["panes"]}
    assert set(groups) == {("side", False), ("side", True)}
    assert len(groups[("side", False)]["panes"]) == 2 and len(groups[("side", True)]["panes"]) == 1
    assert groups[("side", True)]["panes"][0]["centroid"][0] > 0.9
    # the same crop, so no branding check differs between the two
    assert groups[("side", False)]["crop"] == groups[("side", True)]["crop"]


def test_the_rear_window_pane_of_a_rear_seats_photo_gets_the_rear_view(run, cockpit):
    result = run(_enabled_data(), provider=_provider(cockpit[1]), shot_key="rear_seats")
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"], meta
    groups = {(g["view"], g["mirrored"]): g for g in meta["panes"]}
    assert set(groups) == {("rear", False), ("side", False), ("side", True)}
    assert groups[("rear", False)]["blurSigmaPx"] == pytest.approx(1.5 * groups[("side", False)]["blurSigmaPx"])


def test_a_side_crop_that_would_show_the_lettering_leaves_the_original(run, cockpit):
    """A side view set up to see the upper wall overlaps the plate's branding → the photo stays
    the original with the hint, whatever the plate's layout."""
    data = _enabled_data(side=_view(cropHeight=0.9, top=0.0))
    result = run(data, provider=_provider(cockpit[1]), shot_key="driver_seat", keep_views=True)
    assert result.metadata["shotKind"] == "interior_passthrough"
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] is False and meta["reason"] == "branding_in_view" and meta["gate"]["passed"]
    assert meta["crops"][-1]["reason"] == "branding_in_view"
    assert [(w.code, w.message) for w in result.warnings] == [(gb.SKIP_CODE, gb.MESSAGES["branding_in_view"])]
    # the forward view may show it
    data = _enabled_data(forward=_view(cropHeight=0.9, top=0.0, mirrorRight=False))
    result = run(data, provider=_provider(cockpit[1]), shot_key="cockpit", keep_views=True)
    assert result.metadata["shotKind"] == "interior_glass_backdrop"


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


def test_panes_given_by_the_provider_keep_their_views(run, cockpit):
    rgb, alpha = cockpit
    windscreen, quarter, right = (np.where(alpha > 0, alpha, 0.0) * 0 for _ in range(3))
    windscreen = alpha.copy()
    windscreen[:, : QUARTER[2] + 10] = 0.0
    windscreen[:, RIGHT[0] - 10 :] = 0.0
    quarter = alpha.copy()
    quarter[:, QUARTER[2] + 10 :] = 0.0
    panes = (gb.GlassPane(windscreen, "forward"), gb.GlassPane(quarter, "side"))
    result = run(_enabled_data(), provider=_provider(alpha, panes=panes), shot_key="driver_seat")
    meta = result.metadata["glassBackdrop"]
    assert meta["applied"] and sorted((g["view"], g["mirrored"]) for g in meta["panes"]) == [("forward", False), ("side", False)]
    # a pane of the wrong size never reaches the composite
    provider = _provider(alpha, panes=(gb.GlassPane(windscreen[:-1], "forward"),))
    result = run(_enabled_data(), provider=provider)
    assert result.metadata["glassBackdrop"]["reason"] == "mask_invalid"


def test_a_missing_plate_set_leaves_the_original_with_a_hint(run, cockpit, plates_dir):
    (plates_dir / "plates.json").unlink()
    result = run(_enabled_data(), provider=_provider(cockpit[1]), keep_views=True)
    assert result.metadata["shotKind"] == "interior_passthrough"
    assert result.metadata["glassBackdrop"]["reason"] == "showroom_unavailable"
    assert "Showroom-Hintergrund nicht verfügbar" in result.warnings[0].message


def test_the_shipped_plate_branding_boxes_match_the_recorded_ones():
    """The side/rear rule above is checked against the recorded boxes; this keeps them honest
    against the real plate set when it is present (smoke test, like the other real-plate tests)."""
    settings = Settings()
    if not (settings.presets_dir / "autoexperten-haendler" / "plates.json").is_file():
        pytest.skip("real plate set not present")
    preset = load_preset(settings, "autoexperten_standard")
    showroom = BackgroundProvider(settings).get(preset, gb.GLASS_PLATE, 2400, 1800)
    context = gb.PlateContext.from_showroom(showroom)
    assert context.branding_boxes == SHIPPED_BOXES
    assert context.horizon_v == pytest.approx(0.254, abs=1e-3)
