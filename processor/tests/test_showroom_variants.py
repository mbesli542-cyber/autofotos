"""Showroom look variants (public/presets/variants/): reference branding, glossier floor, car
further back in the room. The shipped preset keeps the defaults (current look unchanged)."""

from __future__ import annotations

import copy
import io
import json
import math
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.pipeline.pipeline import process_photo
from app.presets import BackgroundProvider, Placement, PresetConfigError, load_preset, parse_preset
from app.showroom.branding import BrandingConfig, TextConfig, apply_branding
from app.showroom.icons import ICON_NAMES, icon_mask, path_polylines
from app.pipeline import room_depth
from app.showroom import plates as plates_module
from app.showroom.plates import WALL_CLEARANCE_M, load_plate_set, move_proxy_back
from tests.conftest import encode_jpeg

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESETS = REPO_ROOT / "public/presets"
BRAND_DIR = REPO_ROOT / "public/brand"
STANDARD = json.loads((PRESETS / "autoexperten-standard.json").read_text(encoding="utf-8"))
VARIANTS = {
    name: json.loads((PRESETS / "variants" / f"autoexperten-standard-{name}.json").read_text(encoding="utf-8"))
    for name in ("v1", "v2")
}
REAL_PLATES = PRESETS / "autoexperten-standard/plates.json"
NEW_TEXT_FIELDS = ("align", "icon", "rule_length", "rule_gap", "rule_thickness")


def _with(data: dict, path: list, value) -> dict:
    data = copy.deepcopy(data)
    node = data
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return data


# --------------------------------------------------------------------------- configuration


def test_the_shipped_preset_keeps_the_current_look():
    preset = parse_preset(STANDARD)
    assert preset.placement.width_scale == 1.0 and preset.placement.depth_offset_m == 0.0
    assert not preset.shot_placement
    for name in ("city", "website", "phone"):
        text = getattr(preset.branding, name)
        default = TextConfig()
        assert all(getattr(text, f) == getattr(default, f) for f in NEW_TEXT_FIELDS), name
    # the new fields are written out explicitly (nothing hidden behind a default)
    assert {"widthScale", "depthOffsetM"} <= set(STANDARD["placement"])
    for name in ("city", "website", "phone"):
        assert {"align", "icon", "ruleLength", "ruleGap", "ruleThickness"} <= set(STANDARD["branding"][name])


def test_new_placement_defaults_change_nothing():
    names = {f.name for f in fields(Placement)}
    assert {"width_scale", "depth_offset_m"} <= names
    assert Placement().width_scale == 1.0 and Placement().depth_offset_m == 0.0


@pytest.mark.parametrize("name", ["v1", "v2"])
def test_variants_are_complete_valid_presets_of_the_same_showroom(name):
    data = VARIANTS[name]
    preset = parse_preset(data)
    assert data["id"] == STANDARD["id"] and data["background"] == STANDARD["background"]
    # only the look changes – output, shadow, adjustments and the quality gate stay the same
    for section in ("output", "shadow", "vehicleAdjustments", "quality"):
        assert data[section] == STANDARD[section], section
    branding = preset.branding
    assert branding.logo.file == "official/AutoExperten_Logo.png"  # the official file, only scaled
    assert branding.logo.max_width > parse_preset(STANDARD).branding.logo.max_width
    assert branding.city.text == "Schwetzingen" and branding.city.rule_length > 0
    assert (branding.website.icon, branding.phone.icon) == ("globe", "phone")
    assert branding.website.text == "www.autoexperten-rn.de" and branding.phone.text == "+49 6202 9262357"
    assert branding.website.align == branding.phone.align == "left"
    # glossier floor, still within the reflection limits
    assert 1.0 < preset.reflection.strength <= 1.5 and preset.reflection.max_reflectance <= 0.3
    assert (preset.placement.depth_offset_m > 0) == (name == "v2")
    # the new placement fields are written out (copying a variant over the standard file must
    # not bring back silently defaulted fields)
    assert {"widthScale", "depthOffsetM"} <= set(data["placement"])


#: Branding keeps this share of the brand wall (between the slat panels) free on each side.
BRAND_EDGE_MARGIN = 0.04


@pytest.mark.parametrize("name", ["standard", "v1", "v2"])
def test_branding_keeps_clear_of_the_brand_wall_edges(name):
    data = STANDARD if name == "standard" else VARIANTS[name]
    preset = parse_preset(data)
    width, height = 2680, 1680  # brand area 6.7 m × 4.2 m
    _, layout = apply_branding(_wall(width, height), _flat(preset.branding), BRAND_DIR)
    assert {b[0] for b in layout.boxes} == {"logo", "city", "website", "phone"}
    for label, x0, y0, x1, y1 in layout.boxes:
        assert x0 >= BRAND_EDGE_MARGIN * width and x1 <= (1 - BRAND_EDGE_MARGIN) * width, label
    # no element runs into another one
    for i, a in enumerate(layout.boxes):
        for b in layout.boxes[i + 1 :]:
            overlap_x = min(a[3], b[3]) - max(a[1], b[1])
            overlap_y = min(a[4], b[4]) - max(a[2], b[2])
            assert overlap_x <= 0 or overlap_y <= 0, (a[0], b[0])


@pytest.mark.parametrize(
    "path, value",
    [
        (["placement", "widthScale"], 0.5),
        (["placement", "widthScale"], 1.5),
        (["placement", "depthOffsetM"], -0.2),
        (["placement", "depthOffsetM"], 2.5),
        (["shotPlacement", "front", "widthScale"], 2.0),
        (["branding", "website", "align"], "right"),
        (["branding", "website", "icon"], "star"),
        (["branding", "website", "icon"], 1),
        (["branding", "city", "ruleLength"], 0.6),
        (["branding", "city", "ruleGap"], 0.3),
        (["branding", "city", "ruleThickness"], 0.05),
        (["branding", "city", "ruleLenght"], 0.1),
    ],
)
def test_invalid_variant_values_are_configuration_errors(path, value):
    with pytest.raises(PresetConfigError):
        parse_preset(_with(STANDARD, path, value))


def test_side_lines_need_a_thickness():
    data = _with(STANDARD, ["branding", "city", "ruleLength"], 0.1)
    data = _with(data, ["branding", "city", "ruleThickness"], 0.0)
    with pytest.raises(PresetConfigError, match="ruleThickness"):
        parse_preset(data)


# --------------------------------------------------------------------------- icons and text decoration


def test_lucide_icons_are_rasterised_as_line_drawings():
    for name in ICON_NAMES:
        mask = icon_mask(name, 96)
        assert mask.shape == (96, 96) and 0.1 < mask.mean() < 0.5  # 2/24 strokes
        assert mask.max() > 0.95 and mask[:2].max() < 0.5  # inside the 24 px view box margin
    globe = icon_mask("globe", 96)
    assert np.abs(globe - globe[:, ::-1]).mean() < 0.03  # symmetric (up to anti-aliasing)
    assert globe[48, 48] > 0.9  # equator and meridian cross in the centre


def test_svg_arcs_end_exactly_at_their_end_points():
    (line,) = path_polylines("M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20")
    assert line[0] == (12.0, 2.0)
    assert any(abs(x - 12) < 1e-9 and abs(y - 22) < 1e-9 for x, y in line)
    assert line[-1] == pytest.approx((12.0, 2.0))
    xs = [p[0] for p in line]
    assert min(xs) > 6 and max(xs) < 18  # a lens, not a circle


def _flat(cfg: BrandingConfig) -> BrandingConfig:
    return replace(cfg, light_match=0.0, mount=replace(cfg.mount, shadow_opacity=0.0))


def _wall(width=2400, height=1800, value=232):
    return np.full((height, width, 3), value, np.uint8)


def test_explicit_default_text_fields_draw_the_same_pixels():
    cfg = BrandingConfig()
    explicit = replace(
        cfg, city=replace(cfg.city, align="center", icon="", rule_length=0.0, rule_gap=0.0, rule_thickness=0.0)
    )
    a, la = apply_branding(_wall(), cfg, BRAND_DIR)
    b, lb = apply_branding(_wall(), explicit, BRAND_DIR)
    assert np.array_equal(a, b) and la == lb


def test_side_lines_frame_the_city_name():
    cfg = _flat(BrandingConfig())
    plain = replace(cfg, website=replace(cfg.website, text=""), phone=replace(cfg.phone, text=""))
    lined = replace(plain, city=replace(plain.city, rule_length=0.1, rule_gap=0.02, rule_thickness=0.002))
    _, base = apply_branding(_wall(), plain, BRAND_DIR)
    out, layout = apply_branding(_wall(), lined, BRAND_DIR)
    bx0, by0, bx1, by1 = base.box("city")
    x0, y0, x1, y1 = layout.box("city")
    width = 2400
    assert x0 == pytest.approx(bx0 - 0.12 * width, abs=3) and x1 == pytest.approx(bx1 + 0.12 * width, abs=3)
    assert (x0 + x1) / 2 == pytest.approx((bx0 + bx1) / 2, abs=2)  # the name stays centred
    dark = out.astype(np.int16).sum(axis=-1) < 3 * 200
    left_line = dark[y0:y1, x0 + 5 : x0 + int(0.1 * width) - 5]
    rows = np.nonzero(left_line.all(axis=1))[0]
    assert 1 <= len(rows) <= 6  # a thin continuous line …
    assert y0 + rows.mean() > (by0 + by1) / 2 - 0.4 * (by1 - by0)  # … at the lowercase letters
    assert not dark[y0:y1, bx0 - int(0.02 * width) + 3 : bx0 - 3].any()  # gap to the text


def test_left_aligned_texts_with_icons_line_up():
    cfg = _flat(BrandingConfig())
    website = replace(cfg.website, align="left", icon="globe", center_x=0.7)
    phone = replace(cfg.phone, align="left", icon="phone", center_x=0.7)
    out, layout = apply_branding(_wall(), replace(cfg, website=website, phone=phone), BRAND_DIR)
    w, p = layout.box("website"), layout.box("phone")
    assert w[0] == p[0] == round(0.7 * 2400)
    _, plain = apply_branding(_wall(), cfg, BRAND_DIR)
    pw = plain.box("website")
    assert (w[2] - w[0]) > (pw[2] - pw[0])  # icon + gap before the text
    icon_cols = out[w[1] : w[3], w[0] : w[0] + 10].astype(np.int16).sum(axis=-1) < 3 * 200
    assert icon_cols.any()


# --------------------------------------------------------------------------- car further back in the room


@pytest.fixture(scope="module")
def plates():
    return load_plate_set(REAL_PLATES)


def test_no_offset_returns_the_plate_itself(plates):
    plate = plates.plate("front_left_45")
    assert move_proxy_back(plate, 0.0) == (plate, 0.0)


@pytest.mark.parametrize("key", ["front_left_45", "rear_left_45", "left_side", "front"])
def test_moving_back_is_perspective_consistent(plates, key):
    plate = plates.plate(key)
    moved, applied = move_proxy_back(plate, 0.3)
    assert 0 < applied <= 0.3
    # further back: higher in the frame, smaller, still centred; contacts on the floor homography
    assert moved.ground_v < plate.ground_v
    assert moved.target_width_ratio < plate.target_width_ratio
    for label, contact in moved.proxy_contacts.items():
        u, v = plate.floor_to_uv(*contact.floor)
        assert (contact.u, contact.v) == pytest.approx((float(u), float(v)), abs=1e-9)
        old = plate.proxy_contacts[label].floor
        assert math.hypot(contact.floor[0] - old[0], contact.floor[1] - old[1]) == pytest.approx(applied)
    shift = np.asarray(moved.proxy_shift)
    assert np.linalg.norm(shift) == pytest.approx(applied)
    # width shrinks like 1 / distance from the camera
    inv = np.linalg.inv(plate.floor_h)
    ref = inv @ np.array([0.5, plate.ground_v, 1.0])
    ref = ref[:2] / ref[2]
    cam = np.asarray(plate.camera.position, np.float64)

    def dist(p):
        return math.dist((p[0], p[1], 0.0), cam)

    expected = dist(ref) / dist(ref + shift)
    assert moved.target_width_ratio / plate.target_width_ratio == pytest.approx(expected, rel=0.03)
    u, _ = plate.floor_to_uv(*(ref + shift))
    assert float(u) == pytest.approx(0.5, abs=1e-6)  # moved along the viewing ray of the centre column
    # bbox shrinks about the ground line, aspect kept
    assert moved.proxy_aspect() == pytest.approx(plate.proxy_aspect(), rel=1e-6)


@pytest.mark.parametrize("key", ["front_left_45", "front", "rear_right_45"])
def test_the_car_never_goes_into_the_wall(plates, key):
    plate = plates.plate(key)
    moved, applied = move_proxy_back(plate, 5.0)
    assert applied < 5.0
    c = moved.proxy_contacts
    centre = np.mean([np.asarray(p.floor) for p in c.values()], axis=0)
    front = 0.5 * (np.asarray(c["front_left"].floor) + np.asarray(c["front_right"].floor))
    rear = 0.5 * (np.asarray(c["rear_left"].floor) + np.asarray(c["rear_right"].floor))
    axis = (front - rear) / np.linalg.norm(front - rear)
    side = np.array([-axis[1], axis[0]])
    corners = [
        centre + i * 0.5 * plate.proxy["lengthM"] * axis + j * 0.5 * plate.proxy["widthM"] * side
        for i in (-1, 1)
        for j in (-1, 1)
    ]
    assert max(p[1] for p in corners) == pytest.approx(-WALL_CLEARANCE_M, abs=1e-6)


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def test_pipeline_places_the_car_further_back_with_its_shadow(settings, fake_segmenter, vehicle):
    base = load_preset(settings, "autoexperten_standard")
    back = replace(base, placement=replace(base.placement, depth_offset_m=0.45))
    backgrounds = BackgroundProvider(settings)
    data = encode_jpeg(vehicle[0], 97)
    near = process_photo(data, preset=base, segmenter=fake_segmenter, backgrounds=backgrounds, shot_key="left_side")
    far = process_photo(data, preset=back, segmenter=fake_segmenter, backgrounds=backgrounds, shot_key="left_side")
    pn, pf = near.metadata["placement"], far.metadata["placement"]
    assert "depthOffsetM" not in pn and pf["depthOffsetM"] > 0.3
    assert pf["achievedWidthRatio"] < pn["achievedWidthRatio"]
    assert pf["groundY"] < pn["groundY"]
    # the contact shadow follows the car: darkest floor row under the car moves up with it
    for result, placement in ((near, pn), (far, pf)):
        out = _decode(result.jpeg).astype(np.float32).mean(axis=-1)
        cx = int(placement["left"] + placement["width"] / 2)
        bottom = int(placement["top"] + placement["height"])
        below = out[bottom + 2 : bottom + 40, cx - 50 : cx + 50].mean()
        floor = out[min(bottom + 200, out.shape[0] - 5) :, cx - 50 : cx + 50].mean()
        assert below < floor  # shadow right under the car, not where the car was before


# --------------------------------------------------------------------------- per-photo room fit


def _wall_clearance(metadata: dict) -> float:
    """Floor between the car's fitted footprint (the final ground model) and the wall (m)."""
    fp = metadata["grounding"]["footprint"]
    (cx, cy), (ux, uy) = fp["centreM"], fp["axis"]
    vx, vy = -uy, ux
    corners = [
        cy + a * uy + b * vy for a in (fp["frontM"], -fp["rearM"]) for b in (fp["leftM"], -fp["rightM"])
    ]
    return -max(corners)


def _run(settings, segmenter, photo, shot, preset):
    return process_photo(
        encode_jpeg(photo[0], 97), preset=preset, segmenter=segmenter, backgrounds=BackgroundProvider(settings),
        shot_key=shot,
    )  # fmt: skip


def _variant(preset, depth=0.0, scale=1.0):
    return replace(preset, placement=replace(preset.placement, depth_offset_m=depth, width_scale=scale))


def test_the_default_placement_runs_no_room_fit(settings, fake_segmenter, three_quarter, monkeypatch):
    calls = []
    original = room_depth.footprint_wall_y
    monkeypatch.setattr(room_depth, "footprint_wall_y", lambda *a: calls.append(1) or original(*a))
    base = load_preset(settings, "autoexperten_standard")
    result = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    assert not calls
    assert "roomFit" not in result.metadata["placement"] and "depthOffsetM" not in result.metadata["placement"]


def test_moving_back_never_binds_the_floor_harder(settings, fake_segmenter, three_quarter, monkeypatch):
    # a deep room (no wall limits): moving back far enough brings the far tyres to the wall
    # line – there the car would only be shrunk on its spot (toy car); the fit stops before
    monkeypatch.setattr(plates_module, "WALL_CLEARANCE_M", -2.0)
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", -2.0)
    base = load_preset(settings, "autoexperten_standard")
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base).metadata["placement"]
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 2.0)).metadata["placement"]
    fit = far["roomFit"]
    assert fit["reducedBy"] == ["floor"] and 0.5 < fit["depthOffsetM"] < 2.0
    base_floor = near["targetFraction"] if near["limitedBy"] == "floor" else 1.0
    assert far["limitedBy"] != "floor" or far["targetFraction"] >= base_floor - room_depth.FLOOR_TOLERANCE
    assert far["targetFraction"] >= 0.99  # the car has the size perspective gives it there …
    assert far["achievedWidthRatio"] < near["achievedWidthRatio"]  # … smaller because further back
    assert far["groundY"] < near["groundY"]


def test_the_fitted_footprint_keeps_the_wall_clearance(settings, fake_segmenter, three_quarter, monkeypatch):
    # a long car / short room: the real footprint (not the plate's proxy) needs 1.3 m to the wall
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", 1.3)
    base = load_preset(settings, "autoexperten_standard")
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    assert _wall_clearance(near.metadata) > 1.4
    _, proxy_limit = move_proxy_back(
        load_plate_set(settings.assets_dir / "presets/autoexperten-standard/plates.json").plate("front_left_45"), 2.0
    )
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 2.0))
    fit = far.metadata["placement"]["roomFit"]
    assert fit["reducedBy"] == ["wall"]
    assert 0.0 < fit["depthOffsetM"] < proxy_limit - 0.1  # the proxy alone would go further
    assert _wall_clearance(far.metadata) >= 1.3 - 0.03  # the final ground model agrees
    assert far.metadata["placement"]["achievedWidthRatio"] < near.metadata["placement"]["achievedWidthRatio"]


def test_a_variant_never_brings_the_car_closer_to_the_wall_than_the_plate_spot(
    settings, fake_segmenter, three_quarter, monkeypatch
):
    # the plate's own spot leaves less than the clearance: depth offset and enlargement may not
    # bring the car any closer (it stays on the spot, at the plate's size)
    monkeypatch.setattr(room_depth, "WALL_CLEARANCE_M", 3.0)
    base = load_preset(settings, "autoexperten_standard")
    near = _run(settings, fake_segmenter, three_quarter, "front_left_45", base)
    far = _run(settings, fake_segmenter, three_quarter, "front_left_45", _variant(base, 0.45, 1.06))
    fit = far.metadata["placement"]["roomFit"]
    assert "wall" in fit["reducedBy"]
    assert fit["depthOffsetM"] < 0.02 and fit["widthScale"] < 1.005
    assert _wall_clearance(far.metadata) >= _wall_clearance(near.metadata) - 0.03
